"""
APScheduler-backed job runner.
Each job opens a fresh SSH connection, executes via the Command Dispatcher
(so registered commands like sys:memory, net:ping, etc. work correctly on Alpine).
Multi-line bash scripts are executed directly via SSH.
"""
import asyncio
import logging
import traceback
from datetime import datetime, timedelta

import paramiko
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.db.database import SessionLocal
from app.db import models

log = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="UTC", job_defaults={"misfire_grace_time": 60})


# ── SSH open/close ─────────────────────────────────────────────────────────────

def _open_ssh(vm: models.VirtualMachine) -> paramiko.SSHClient:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname=vm.host, username=vm.username,
                password=vm.password, port=22, timeout=15)
    return ssh


# ── Command execution ─────────────────────────────────────────────────────────

def _run(ssh: paramiko.SSHClient, script: str) -> tuple[bool, str]:
    """
    Route command correctly:
      - Multi-line → raw bash script via exec_command
      - Single-line → through Command Dispatcher (handles sys:*, net:*, diag:*, etc.)
    """
    script = script.strip()

    if "\n" in script:
        # Raw multi-line bash script
        _, stdout, stderr = ssh.exec_command(f"sh -c {repr(script)}", timeout=60)
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()
        exit_code = stdout.channel.recv_exit_status()
        output = out if out else err
        return exit_code == 0, output or "(no output)"

    # Single-line: route through the dispatcher so registered commands work
    from app.core.command_dispatcher import dispatcher
    parsed = dispatcher.parse(script)
    try:
        result = asyncio.run(dispatcher.execute(parsed, ssh))
        return result.success, (result.output or result.error or "(no output)").strip()
    except Exception as exc:
        return False, f"Dispatcher error: {exc}"


# ── Core executor ─────────────────────────────────────────────────────────────

def _execute_job(job_id: int):
    """Called by APScheduler in a background thread."""
    db = SessionLocal()
    try:
        job = db.query(models.ScheduledJob).get(job_id)
        if not job or job.status == "cancelled":
            return

        vm = db.query(models.VirtualMachine).get(job.vm_id)
        if not vm:
            job.status = "failed"
            job.last_output = "VM not found"
            db.commit()
            return

        job.status = "running"
        job.last_run_at = datetime.utcnow()
        db.commit()

        try:
            ssh = _open_ssh(vm)
            try:
                success, output = _run(ssh, job.script)
            finally:
                ssh.close()
        except Exception as exc:
            success, output = False, f"SSH connection failed: {exc}"

        job.status = "completed" if success else "failed"
        job.last_success = success
        job.last_output = output[:4000]
        job.run_count = (job.run_count or 0) + 1

        if job.job_type == "once":
            job.next_run_at = None
        else:
            unit = job.interval_unit or "minutes"
            val = job.interval_value or 5
            delta = {"minutes": timedelta(minutes=val),
                     "hours": timedelta(hours=val),
                     "days": timedelta(days=val)}.get(unit, timedelta(minutes=val))
            job.next_run_at = datetime.utcnow() + delta

        db.commit()
    except Exception:
        log.error("Job %s crashed: %s", job_id, traceback.format_exc())
        try:
            job = db.query(models.ScheduledJob).get(job_id)
            if job:
                job.status = "failed"
                job.last_output = traceback.format_exc()[:2000]
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


# ── Public API ────────────────────────────────────────────────────────────────

def schedule_job(job: models.ScheduledJob):
    aps_id = f"job_{job.id}"

    if job.job_type == "once":
        trigger = DateTrigger(run_date=job.run_at, timezone="UTC")
    else:
        unit = job.interval_unit or "minutes"
        val = job.interval_value or 5
        kwargs = {unit: val}
        trigger = IntervalTrigger(**kwargs, timezone="UTC",
                                  start_date=datetime.utcnow() + timedelta(seconds=5))

    scheduler.add_job(
        _execute_job, trigger=trigger, args=[job.id],
        id=aps_id, replace_existing=True,
    )

    aps_job = scheduler.get_job(aps_id)
    if aps_job and aps_job.next_run_time:
        db = SessionLocal()
        try:
            j = db.query(models.ScheduledJob).get(job.id)
            if j:
                j.next_run_at = aps_job.next_run_time.replace(tzinfo=None)
                db.commit()
        finally:
            db.close()


def cancel_job(job_id: int):
    try:
        scheduler.remove_job(f"job_{job_id}")
    except Exception:
        pass


def trigger_now(job_id: int):
    from threading import Thread
    Thread(target=_execute_job, args=(job_id,), daemon=True).start()


def start_scheduler():
    if not scheduler.running:
        scheduler.start()

    # Alert checker runs every 60 seconds
    from app.core.alert_checker import check_all_alerts
    scheduler.add_job(
        check_all_alerts,
        IntervalTrigger(seconds=60, timezone="UTC"),
        id="alert_checker",
        replace_existing=True,
    )

    db = SessionLocal()
    try:
        active = db.query(models.ScheduledJob).filter(
            models.ScheduledJob.status.in_(["pending", "completed"]),
            models.ScheduledJob.job_type == "interval"
        ).all()
        for job in active:
            try:
                schedule_job(job)
            except Exception:
                pass

        future_once = db.query(models.ScheduledJob).filter(
            models.ScheduledJob.job_type == "once",
            models.ScheduledJob.status == "pending",
            models.ScheduledJob.run_at > datetime.utcnow()
        ).all()
        for job in future_once:
            try:
                schedule_job(job)
            except Exception:
                pass
    finally:
        db.close()
