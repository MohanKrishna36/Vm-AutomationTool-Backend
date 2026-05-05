"""
Background alert checker – runs every 60 s via APScheduler.
Opens one SSH connection per VM that has enabled rules, fetches metrics,
creates AlertEvent on threshold breach, resolves when condition clears.
"""
import logging
import paramiko
from datetime import datetime

from app.db.database import SessionLocal
from app.db import models

log = logging.getLogger(__name__)

# Alpine-compatible metric commands that return a single float on stdout
METRIC_COMMANDS = {
    "memory":   "free | grep Mem | awk '{printf \"%.2f\", ($3/$2)*100}'",
    "disk":     "df / | tail -1 | awk '{print $5}' | tr -d '%'",
    "cpu_load": "cat /proc/loadavg | awk '{print $1}'",
}

METRIC_LABELS = {
    "memory":   "Memory Usage",
    "disk":     "Disk Usage (/)",
    "cpu_load": "CPU Load (1m avg)",
}

METRIC_UNITS = {
    "memory":   "%",
    "disk":     "%",
    "cpu_load": "",
}


def _open_ssh(vm) -> paramiko.SSHClient:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname=vm.host, username=vm.username,
                password=vm.password, port=22, timeout=10)
    return ssh


def _fetch_metric(ssh: paramiko.SSHClient, metric: str) -> float | None:
    cmd = METRIC_COMMANDS.get(metric)
    if not cmd:
        return None
    try:
        _, stdout, _ = ssh.exec_command(cmd, timeout=15)
        raw = stdout.read().decode("utf-8", errors="replace").strip()
        return float(raw) if raw else None
    except Exception:
        return None


def _triggered(value: float, operator: str, threshold: float) -> bool:
    return {
        "gt":  value >  threshold,
        "lt":  value <  threshold,
        "gte": value >= threshold,
        "lte": value <= threshold,
    }.get(operator, False)


def check_all_alerts():
    """Called by APScheduler every 60 seconds."""
    db = SessionLocal()
    try:
        rules = db.query(models.AlertRule).filter(
            models.AlertRule.enabled == True
        ).all()
        if not rules:
            return

        # Group rules by vm_id → one SSH connection per VM
        vm_rule_map: dict[int, list] = {}
        for rule in rules:
            vm_rule_map.setdefault(rule.vm_id, []).append(rule)

        for vm_id, vm_rules in vm_rule_map.items():
            vm = db.query(models.VirtualMachine).get(vm_id)
            if not vm:
                continue

            try:
                ssh = _open_ssh(vm)
            except Exception as exc:
                log.warning("Alert checker: SSH to %s failed: %s", vm.host, exc)
                continue

            try:
                # Deduplicate metric fetches
                needed = {r.metric for r in vm_rules}
                values: dict[str, float | None] = {m: _fetch_metric(ssh, m) for m in needed}

                for rule in vm_rules:
                    val = values.get(rule.metric)
                    if val is None:
                        continue

                    fired = _triggered(val, rule.operator, rule.threshold)

                    active_event = db.query(models.AlertEvent).filter(
                        models.AlertEvent.rule_id == rule.id,
                        models.AlertEvent.is_active == True,
                    ).first()

                    if fired and not active_event:
                        db.add(models.AlertEvent(
                            rule_id=rule.id,
                            vm_id=vm_id,
                            user_id=rule.user_id,
                            rule_name=rule.name,
                            vm_host=vm.host,
                            metric=rule.metric,
                            operator=rule.operator,
                            current_value=round(val, 2),
                            threshold=rule.threshold,
                        ))
                        log.info("Alert TRIGGERED: %s on %s — %s=%.2f %s %.2f",
                                 rule.name, vm.host, rule.metric, val, rule.operator, rule.threshold)

                    elif not fired and active_event:
                        active_event.is_active = False
                        active_event.resolved_at = datetime.utcnow()
                        active_event.resolved_value = round(val, 2)
                        log.info("Alert RESOLVED: %s on %s — %s=%.2f",
                                 rule.name, vm.host, rule.metric, val)

                    elif fired and active_event:
                        # Keep current_value up-to-date while still firing
                        active_event.current_value = round(val, 2)

            finally:
                ssh.close()

        db.commit()

    except Exception as exc:
        log.error("Alert checker crashed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()
