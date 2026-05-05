from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from pydantic import BaseModel
import paramiko

from app.core.deps import get_current_user
from app.db.database import SessionLocal
from app.db import models

# OAuth2 scheme (for auth enforcement + Swagger)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

router = APIRouter(
    prefix="/vm",
    tags=["vm"],
    dependencies=[Depends(oauth2_scheme)]
)


# ---------- DB DEPENDENCY ----------

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------- REQUEST MODELS ----------

class VMConnectRequest(BaseModel):
    host: str
    username: str
    password: str


class BroadcastRequest(BaseModel):
    vm_ids: List[int]
    command: str


# ---------- ROUTES ----------

# 1️⃣ LIST VMS
@router.get("/list")
def list_vms(
    current_user=Security(get_current_user),
    db: Session = Depends(get_db)
):
    vms = db.query(models.VirtualMachine).all()

    return [
        {
            "id": vm.id,
            "host": vm.host,
            "is_busy": vm.is_busy,
            "locked_by": vm.locked_by,
            "locked_by_me": vm.is_busy and vm.locked_by == current_user.id
        }
        for vm in vms
    ]


# 2️⃣ CONNECT (OR CREATE) VM
@router.post("/connect")
def connect_vm(
    data: VMConnectRequest,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db)
):
    vm = db.query(models.VirtualMachine).filter(
        models.VirtualMachine.host == data.host
    ).first()

    # CASE 1: VM EXISTS
    if vm:
        if vm.is_busy:
            raise HTTPException(
                status_code=409,
                detail="VM is currently busy"
            )

        # Reuse VM record
        vm.username = data.username
        vm.password = data.password
        vm.is_busy = True
        vm.locked_by = current_user.id
        db.commit()

        return {"message": "Connected to existing VM"}

    # CASE 2: VM DOES NOT EXIST → CREATE
    new_vm = models.VirtualMachine(
        host=data.host,
        username=data.username,
        password=data.password,
        is_busy=True,
        locked_by=current_user.id
    )

    db.add(new_vm)
    db.commit()

    return {"message": "VM created and connected"}


# 3️⃣ RUN COMMAND ON VM
@router.post("/command")
def run_command(
    host: str,
    command: str,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db)
):
    vm = db.query(models.VirtualMachine).filter(
        models.VirtualMachine.host == host
    ).first()

    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")

    if not vm.is_busy or vm.locked_by != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="You do not own this VM"
        )

    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        ssh.connect(
            hostname=vm.host,
            username=vm.username,
            password=vm.password,
            timeout=10
        )

        stdin, stdout, stderr = ssh.exec_command(command)

        output = stdout.read().decode()
        error = stderr.read().decode()

        ssh.close()

        return {
            "host": host,
            "command": command,
            "output": output,
            "error": error
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class DisconnectRequest(BaseModel):
    host: str

class DisconnectByIdRequest(BaseModel):
    vm_id: int

# 4️⃣ DISCONNECT VM
@router.post("/disconnect")
def disconnect_vm(
    data: DisconnectRequest,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db)
):
    vm = db.query(models.VirtualMachine).filter(
        models.VirtualMachine.host == data.host
    ).first()

    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")

    if vm.locked_by != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="You do not own this VM"
        )

    vm.is_busy = False
    vm.locked_by = None
    db.commit()

    return {"message": "VM disconnected"}

# 4️⃣🔧 DISCONNECT VM BY ID (fallback)
@router.post("/disconnect/{vm_id}")
def disconnect_vm_by_id(
    vm_id: int,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db)
):
    vm = db.query(models.VirtualMachine).get(vm_id)

    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")

    if vm.locked_by != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="You do not own this VM"
        )

    vm.is_busy = False
    vm.locked_by = None
    db.commit()

    return {"message": "VM disconnected"}


# 5️⃣ LIVE METRICS
@router.get("/{vm_id}/metrics")
def get_vm_metrics(
    vm_id: int,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    vm = db.query(models.VirtualMachine).get(vm_id)
    if not vm:
        raise HTTPException(404, "VM not found")
    if not vm.is_busy or vm.locked_by != current_user.id:
        raise HTTPException(403, "VM not connected or not owned by you")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(hostname=vm.host, username=vm.username, password=vm.password, port=22, timeout=10)
    except Exception as exc:
        raise HTTPException(503, f"SSH failed: {exc}")

    def _run(cmd):
        try:
            _, stdout, _ = ssh.exec_command(cmd, timeout=10)
            return stdout.read().decode("utf-8", errors="replace").strip()
        except Exception:
            return ""

    try:
        mem_raw    = _run("free | grep Mem | awk '{printf \"%.1f\", ($3/$2)*100}'")
        disk_raw   = _run("df / | tail -1 | awk '{print $5}' | tr -d '%'")
        cpu_raw    = _run("cat /proc/loadavg | awk '{print $1}'")
        uptime_raw = _run("cat /proc/uptime | awk '{print $1}'")
        hostname   = _run("hostname")
        cpu_cores  = _run("nproc")
        total_mem  = _run("free -m | grep Mem | awk '{print $2}'")
        used_mem   = _run("free -m | grep Mem | awk '{print $3}'")
    finally:
        ssh.close()

    def _f(v, default=0.0):
        try:
            return float(v)
        except Exception:
            return default

    return {
        "vm_id":          vm_id,
        "host":           vm.host,
        "hostname":       hostname or vm.host,
        "memory_pct":     _f(mem_raw),
        "disk_pct":       _f(disk_raw),
        "cpu_load":       _f(cpu_raw),
        "cpu_cores":      int(_f(cpu_cores, 1)),
        "mem_total_mb":   int(_f(total_mem)),
        "mem_used_mb":    int(_f(used_mem)),
        "uptime_seconds": _f(uptime_raw),
        "timestamp":      datetime.utcnow().isoformat() + "Z",
    }


# 6️⃣ BROADCAST COMMAND TO MULTIPLE VMs
@router.post("/broadcast")
def broadcast_command(
    req: BroadcastRequest,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    if not req.vm_ids:
        raise HTTPException(400, "No VMs selected")
    if not req.command.strip():
        raise HTTPException(400, "Empty command")

    vms_to_run, skipped = [], []
    for vm_id in req.vm_ids:
        vm = db.query(models.VirtualMachine).get(vm_id)
        if not vm:
            skipped.append({"vm_id": vm_id, "host": None, "reason": "not found"})
            continue
        if not vm.is_busy or vm.locked_by != current_user.id:
            skipped.append({"vm_id": vm_id, "host": vm.host, "reason": "not connected or not owned"})
            continue
        vms_to_run.append(vm)

    if not vms_to_run:
        raise HTTPException(400, "No connected VMs owned by you in the selection")

    def _exec(vm):
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            ssh.connect(hostname=vm.host, username=vm.username, password=vm.password, port=22, timeout=10)
            _, stdout, stderr = ssh.exec_command(req.command.strip(), timeout=30)
            out  = stdout.read().decode("utf-8", errors="replace").strip()
            err  = stderr.read().decode("utf-8", errors="replace").strip()
            code = stdout.channel.recv_exit_status()
            return {
                "vm_id": vm.id, "host": vm.host,
                "success": code == 0,
                "output": (out or err or "(no output)")[:2000],
                "error": None, "exit_code": code,
            }
        except Exception as exc:
            return {
                "vm_id": vm.id, "host": vm.host,
                "success": False, "output": None,
                "error": str(exc)[:500], "exit_code": -1,
            }
        finally:
            try:
                ssh.close()
            except Exception:
                pass

    import time as _time

    def _exec_timed(vm):
        t0 = _time.monotonic()
        result = _exec(vm)
        result["execution_time_ms"] = round((_time.monotonic() - t0) * 1000, 1)
        return result

    results = []
    with ThreadPoolExecutor(max_workers=min(len(vms_to_run), 10)) as pool:
        futures = {pool.submit(_exec_timed, vm): vm for vm in vms_to_run}
        for future in as_completed(futures):
            results.append(future.result())

    # Persist every result to command_history so reports are accurate
    for r in results:
        row = models.CommandHistory(
            user_id=current_user.id,
            vm_id=r["vm_id"],
            vm_host=r["host"],
            command=req.command.strip(),
            category="raw",
            action="broadcast",
            output=r.get("output") or "",
            error=r.get("error") or "",
            success=r["success"],
            execution_time_ms=r.get("execution_time_ms"),
            executed_at=datetime.utcnow(),
        )
        db.add(row)
    db.commit()

    results.sort(key=lambda r: r["host"])

    return {
        "command":   req.command.strip(),
        "vm_count":  len(vms_to_run),
        "succeeded": sum(1 for r in results if r["success"]),
        "results":   results,
        "skipped":   skipped,
    }
