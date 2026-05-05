import asyncio
import json

import paramiko
from fastapi import APIRouter, Depends, HTTPException, Security, WebSocket
from sqlalchemy.orm import Session

from app.core.command_dispatcher import dispatcher
from app.core.deps import get_current_user
from app.core.session_manager import SESSION_STORE, SessionRuntime
from app.db import models
from app.db.database import SessionLocal

router = APIRouter(prefix="/session", tags=["session"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── REST endpoints ────────────────────────────────────────────────────────────

@router.get("/check/{vm_id}")
def check_session(
    vm_id: int,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    vm = db.query(models.VirtualMachine).get(vm_id)
    if not vm:
        raise HTTPException(404, "VM not found")

    if vm.locked_by == current_user.id and vm.is_busy:
        session = (
            db.query(models.VMSession)
            .filter(
                models.VMSession.vm_id == vm_id,
                models.VMSession.user_id == current_user.id,
                models.VMSession.status == "active",
            )
            .first()
        )
        if session:
            return {"has_session": True, "session_id": session.id, "vm_locked": True}

    return {
        "has_session": False,
        "session_id": None,
        "vm_locked": vm.is_busy and vm.locked_by == current_user.id,
    }


@router.post("/create/{vm_id}")
def create_session(
    vm_id: int,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    vm = db.query(models.VirtualMachine).get(vm_id)
    if not vm:
        raise HTTPException(404, "VM not found")
    if vm.locked_by != current_user.id:
        raise HTTPException(403, "You do not own this VM")

    session = models.VMSession(vm_id=vm.id, user_id=current_user.id)
    db.add(session)
    db.commit()
    return {"session_id": session.id}


# ── SSH helpers ───────────────────────────────────────────────────────────────

async def _quick_exec(ssh: paramiko.SSHClient, cmd: str, timeout: int = 4) -> str:
    loop = asyncio.get_event_loop()

    def _run():
        try:
            _, stdout, _ = ssh.exec_command(cmd, timeout=timeout)
            return stdout.read().decode(errors="replace").strip()
        except Exception:
            return ""

    try:
        return await asyncio.wait_for(loop.run_in_executor(None, _run), timeout=timeout + 1)
    except Exception:
        return ""


async def _resolve_cd(ssh: paramiko.SSHClient, cwd: str, target: str):
    loop = asyncio.get_event_loop()

    def _run():
        try:
            base = f"cd {cwd} 2>/dev/null && " if cwd not in ("~", "") else ""
            _, stdout, stderr = ssh.exec_command(f"{base}cd {target} && pwd", timeout=5)
            out = stdout.read().decode().strip()
            err = stderr.read().decode().strip()
            return out if out and not err else None
        except Exception:
            return None

    try:
        return await asyncio.wait_for(loop.run_in_executor(None, _run), timeout=6)
    except Exception:
        return None


# ── WebSocket — Dispatcher-powered terminal ───────────────────────────────────

@router.websocket("/ws/{session_id}")
async def session_ws(websocket: WebSocket, session_id: str):
    """
    Every command is routed through the Command Dispatcher:
      - Frontend sends JSON  { "cmd": "ls -la" }
      - Backend parses via dispatcher.parse()
      - Executes via ssh.exec_command() — fast, isolated, no PTY overhead
      - Returns JSON { type, output, category, action, execution_time_ms, cached, cwd }

    Registered commands (sys:status, pkg:install, net:ping …) are handled by
    dedicated handlers. Unknown commands run as raw shell with cwd context.
    Read-only results are cached for 30 s — identical repeat calls return instantly.
    """
    await websocket.accept()
    db = SessionLocal()
    session = None
    ssh = None

    try:
        # ── validate session ──────────────────────────────────────────────────
        session = db.query(models.VMSession).get(session_id)
        if not session or session.status != "active":
            await websocket.send_text(json.dumps({"type": "error", "message": "Invalid or expired session"}))
            await websocket.close()
            return

        vm = db.query(models.VirtualMachine).get(session.vm_id)
        if not vm:
            await websocket.send_text(json.dumps({"type": "error", "message": "VM not found"}))
            await websocket.close()
            return

        # ── open persistent SSH (exec_command, no PTY) ────────────────────────
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
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": f"SSH connection failed: {exc}",
            }))
            await websocket.close()
            return

        SESSION_STORE[str(session_id)] = SessionRuntime(ssh, None)

        # ── fetch initial context in parallel ─────────────────────────────────
        cwd, hostname = await asyncio.gather(
            _quick_exec(ssh, "pwd"),
            _quick_exec(ssh, "hostname"),
        )
        cwd = cwd or "~"
        hostname = hostname or "vm"

        await websocket.send_text(json.dumps({
            "type": "connected",
            "message": (
                f"Connected to {vm.host} — Command Dispatcher active "
                f"({len(dispatcher.get_registry())} commands registered)"
            ),
            "vm_host": vm.host,
            "hostname": hostname,
            "cwd": cwd,
        }))

        # ── command loop ──────────────────────────────────────────────────────
        while True:
            raw = await websocket.receive_text()

            # Accept JSON {"cmd":"..."} and plain text (backward compat)
            try:
                msg = json.loads(raw)
                command = msg.get("cmd", "").strip()
            except (json.JSONDecodeError, ValueError):
                command = raw.strip().rstrip("\n\r")

            if not command:
                continue

            # ── clear ─────────────────────────────────────────────────────────
            if command in ("clear", "cls"):
                await websocket.send_text(json.dumps({"type": "clear"}))
                continue

            # ── cd: track cwd across stateless exec_command calls ─────────────
            if command == "cd" or command.startswith("cd "):
                target = command[3:].strip() if command.startswith("cd ") else "~"
                new_cwd = await _resolve_cd(ssh, cwd, target)
                success = new_cwd is not None
                if success:
                    cwd = new_cwd
                await websocket.send_text(json.dumps({
                    "type": "result",
                    "command_id": "cd",
                    "raw_command": command,
                    "category": "sys",
                    "action": "cd",
                    "output": "" if success else f"cd: {target}: No such file or directory",
                    "success": success,
                    "execution_time_ms": 0,
                    "error": "",
                    "cached": False,
                    "cwd": cwd,
                    "hostname": hostname,
                }))
                continue

            # ── dispatch ──────────────────────────────────────────────────────
            parsed = dispatcher.parse(command)

            # Tell frontend which route the command is taking
            await websocket.send_text(json.dumps({
                "type": "dispatching",
                "command_id": parsed.command_id,
                "category": parsed.category,
                "action": parsed.action,
                "priority": parsed.priority.name,
            }))

            # Execute (dispatcher handles registered handlers + raw shell + cwd)
            result = await dispatcher.execute(parsed, ssh, cwd=cwd)

            await websocket.send_text(json.dumps({
                "type": "result",
                **result.to_dict(),
                "cwd": cwd,
                "hostname": hostname,
            }))

    except Exception as exc:
        print(f"[Session WS] error: {exc}")
    finally:
        sid = str(session_id)
        if sid in SESSION_STORE:
            runtime = SESSION_STORE.pop(sid)
            if runtime and runtime.ssh:
                try:
                    runtime.ssh.close()
                except Exception:
                    pass
        if session:
            try:
                session.status = "closed"
                db.commit()
            except Exception:
                pass
        db.close()
