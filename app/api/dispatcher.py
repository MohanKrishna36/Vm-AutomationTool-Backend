import json
import paramiko
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Security, WebSocket, Query
from jose import jwt, JWTError
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.command_dispatcher import dispatcher, CATEGORY_META
from app.core.deps import get_db, get_current_user
from app.core.security import SECRET_KEY, ALGORITHM
from app.db import models
from app.db.database import SessionLocal

router = APIRouter(prefix="/dispatcher", tags=["dispatcher"])


# ── Pydantic models ───────────────────────────────────────────────────────────

class DispatchRequest(BaseModel):
    vm_id: int
    command: str
    priority: Optional[str] = None   # CRITICAL | HIGH | NORMAL | LOW


class BatchRequest(BaseModel):
    vm_id: int
    commands: list[str]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _open_ssh(vm: models.VirtualMachine) -> paramiko.SSHClient:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(
            hostname=vm.host,
            username=vm.username,
            password=vm.password,
            port=22,
            timeout=10,
        )
    except Exception as exc:
        raise HTTPException(503, f"SSH connection failed: {exc}")
    return ssh


def _get_owned_vm(vm_id: int, user_id: int, db: Session) -> models.VirtualMachine:
    vm = db.query(models.VirtualMachine).get(vm_id)
    if not vm:
        raise HTTPException(404, "VM not found")
    if not vm.is_busy:
        raise HTTPException(400, "VM is not connected. Connect to it first from the Dashboard.")
    if vm.locked_by != user_id:
        raise HTTPException(403, "You do not own this VM")
    return vm


def _apply_priority_prefix(command: str, priority: Optional[str]) -> str:
    if not priority:
        return command
    prefix = {"CRITICAL": "!! ", "HIGH": "! ", "LOW": "~ "}.get(priority.upper(), "")
    return prefix + command


# ── REST Endpoints ────────────────────────────────────────────────────────────

@router.post("/dispatch")
async def dispatch_command(
    req: DispatchRequest,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    """Dispatch a single command to a VM through the command dispatcher."""
    vm = _get_owned_vm(req.vm_id, current_user.id, db)
    ssh = _open_ssh(vm)
    cmd = _apply_priority_prefix(req.command, req.priority)
    parsed = dispatcher.parse(cmd)
    try:
        result = await dispatcher.execute(parsed, ssh)
    finally:
        ssh.close()
    return result.to_dict()


@router.post("/batch")
async def batch_dispatch(
    req: BatchRequest,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    """Dispatch multiple commands sequentially, sorted by priority."""
    vm = _get_owned_vm(req.vm_id, current_user.id, db)
    ssh = _open_ssh(vm)
    try:
        results = await dispatcher.batch_execute(req.commands, ssh)
    finally:
        ssh.close()
    return [r.to_dict() for r in results]


@router.get("/registry")
def get_registry(current_user=Security(get_current_user)):
    """Return all registered command handlers grouped by category."""
    return {
        "commands": dispatcher.get_registry(),
        "grouped": dispatcher.get_registry_grouped(),
        "categories": CATEGORY_META,
    }


@router.get("/history")
def get_history(
    limit: int = Query(100, ge=1, le=500),
    current_user=Security(get_current_user),
):
    """Return the last N dispatched command results (shared in-memory store)."""
    return dispatcher.get_history(limit)


@router.get("/metrics")
def get_metrics(current_user=Security(get_current_user)):
    """Return dispatcher performance metrics."""
    return dispatcher.get_metrics()


# ── WebSocket Endpoint ────────────────────────────────────────────────────────

@router.websocket("/ws/{vm_id}")
async def dispatcher_ws(
    websocket: WebSocket,
    vm_id: int,
    token: str = Query(..., description="JWT Bearer token for auth"),
):
    """
    Real-time command dispatcher over WebSocket.

    Client sends JSON messages:
      { "type": "dispatch", "command": "sys:status" }
      { "type": "batch",    "commands": ["sys:memory", "net:ifconfig"] }
      { "type": "metrics" }
      { "type": "ping" }

    Server sends:
      { "type": "connected",   "vm_host": "...", "message": "..." }
      { "type": "dispatching", "command_id": "...", "category": "...", "action": "...", "priority": "..." }
      { "type": "result",      ...CommandResult fields... }
      { "type": "batch_start", "count": N, "commands": [...] }
      { "type": "batch_complete", "count": N }
      { "type": "metrics",     "data": {...} }
      { "type": "error",       "message": "..." }
      { "type": "pong" }
    """
    await websocket.accept()
    db = SessionLocal()

    try:
        # ── Authenticate via query-param JWT ──────────────────────────────────
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username = payload.get("sub")
            user = db.query(models.User).filter(models.User.username == username).first()
            if not user:
                raise ValueError("user not found")
        except (JWTError, ValueError):
            await websocket.send_json({"type": "error", "message": "Authentication failed"})
            await websocket.close()
            return

        # ── Verify VM ownership ───────────────────────────────────────────────
        vm = db.query(models.VirtualMachine).get(vm_id)
        if not vm:
            await websocket.send_json({"type": "error", "message": "VM not found"})
            await websocket.close()
            return
        if not vm.is_busy or vm.locked_by != user.id:
            await websocket.send_json({
                "type": "error",
                "message": "VM not connected or not owned by you. Connect from the Dashboard first.",
            })
            await websocket.close()
            return

        # ── Open SSH connection ───────────────────────────────────────────────
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            ssh.connect(hostname=vm.host, username=vm.username,
                        password=vm.password, port=22, timeout=10)
        except Exception as exc:
            await websocket.send_json({"type": "error", "message": f"SSH failed: {exc}"})
            await websocket.close()
            return

        await websocket.send_json({
            "type": "connected",
            "vm_host": vm.host,
            "message": f"Dispatcher connected to {vm.host}",
            "registered_commands": len(dispatcher.get_registry()),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })

        # ── Message loop ──────────────────────────────────────────────────────
        try:
            while True:
                raw = await websocket.receive_text()
                msg = json.loads(raw)
                mtype = msg.get("type", "")

                if mtype == "dispatch":
                    command = msg.get("command", "").strip()
                    if not command:
                        await websocket.send_json({"type": "error", "message": "Empty command"})
                        continue

                    parsed = dispatcher.parse(command)

                    await websocket.send_json({
                        "type": "dispatching",
                        "command_id": parsed.command_id,
                        "category": parsed.category,
                        "action": parsed.action,
                        "priority": parsed.priority.name,
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    })

                    result = await dispatcher.execute(parsed, ssh)

                    await websocket.send_json({"type": "result", **result.to_dict()})

                    try:
                        db.add(models.CommandHistory(
                            user_id=user.id, vm_id=vm_id, vm_host=vm.host,
                            command=result.raw_command, category=result.category,
                            action=result.action,
                            output=(result.output or "")[:2000],
                            error=(result.error or "")[:500],
                            success=result.success,
                            execution_time_ms=result.execution_time_ms,
                        ))
                        db.commit()
                    except Exception:
                        db.rollback()

                elif mtype == "batch":
                    commands = msg.get("commands", [])
                    if not commands:
                        await websocket.send_json({"type": "error", "message": "Empty batch"})
                        continue

                    parsed_list = [dispatcher.parse(c) for c in commands]
                    parsed_list.sort(key=lambda p: p.priority)

                    await websocket.send_json({
                        "type": "batch_start",
                        "count": len(parsed_list),
                        "commands": [
                            {"id": p.command_id, "category": p.category,
                             "action": p.action, "priority": p.priority.name}
                            for p in parsed_list
                        ],
                    })

                    for p in parsed_list:
                        await websocket.send_json({
                            "type": "dispatching",
                            "command_id": p.command_id,
                            "category": p.category,
                            "action": p.action,
                            "priority": p.priority.name,
                            "timestamp": datetime.utcnow().isoformat() + "Z",
                        })
                        result = await dispatcher.execute(p, ssh)
                        await websocket.send_json({"type": "result", **result.to_dict()})
                        try:
                            db.add(models.CommandHistory(
                                user_id=user.id, vm_id=vm_id, vm_host=vm.host,
                                command=result.raw_command, category=result.category,
                                action=result.action,
                                output=(result.output or "")[:2000],
                                error=(result.error or "")[:500],
                                success=result.success,
                                execution_time_ms=result.execution_time_ms,
                            ))
                            db.commit()
                        except Exception:
                            db.rollback()

                    await websocket.send_json({
                        "type": "batch_complete",
                        "count": len(parsed_list),
                        "metrics": dispatcher.get_metrics(),
                    })

                elif mtype == "metrics":
                    await websocket.send_json({
                        "type": "metrics",
                        "data": dispatcher.get_metrics(),
                    })

                elif mtype == "ping":
                    await websocket.send_json({
                        "type": "pong",
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    })

        except Exception as exc:
            print(f"[Dispatcher WS] loop error: {exc}")
        finally:
            ssh.close()

    finally:
        db.close()
