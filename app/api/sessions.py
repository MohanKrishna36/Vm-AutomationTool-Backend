from fastapi import APIRouter, Depends, HTTPException, Security
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.db import models
from app.core.deps import get_current_user
from fastapi import WebSocket
import asyncio
import paramiko
from app.core.session_manager import SESSION_STORE, SessionRuntime

router = APIRouter(prefix="/session", tags=["session"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/create/{vm_id}")
def create_session(
    vm_id: int,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db)
):
    vm = db.query(models.VirtualMachine).get(vm_id)

    if not vm:
        raise HTTPException(404, "VM not found")

    if vm.locked_by != current_user.id:
        raise HTTPException(403, "You do not own this VM")

    session = models.VMSession(
        vm_id=vm.id,
        user_id=current_user.id
    )

    db.add(session)
    db.commit()

    return {"session_id": session.id}

@router.websocket("/ws/{session_id}")
async def session_ws(websocket: WebSocket, session_id: str):
    await websocket.accept()

    db = SessionLocal()
    session = db.query(models.VMSession).get(session_id)

    if not session or session.status != "active":
        await websocket.close()
        return

    vm = db.query(models.VirtualMachine).get(session.vm_id)

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print("SSH CONNECT TO HOST:", repr(vm.host))
    print("USERNAME:", repr(vm.username))

    ssh.connect(
    hostname=vm.host,
    username=vm.username,
    password=vm.password,
    port=22,
    timeout=10)


    channel = ssh.get_transport().open_session()
    channel.get_pty()
    channel.invoke_shell()

    SESSION_STORE[session_id] = SessionRuntime(ssh, channel)

    try:
        async def ssh_to_ws():
            while True:
                if channel.recv_ready():
                    await websocket.send_text(channel.recv(1024).decode())
                await asyncio.sleep(0.05)

        async def ws_to_ssh():
            while True:
                data = await websocket.receive_text()
                channel.send(data)

        await asyncio.gather(ssh_to_ws(), ws_to_ssh())

    finally:
        SESSION_STORE.pop(session_id, None)
        channel.close()
        ssh.close()

        session.status = "closed"
        vm.is_busy = False
        vm.locked_by = None
        db.commit()
        db.close()