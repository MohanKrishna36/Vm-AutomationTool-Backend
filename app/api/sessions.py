from fastapi import APIRouter, Depends, HTTPException, Security, WebSocket
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.db import models
from app.core.deps import get_current_user
import asyncio
import paramiko
from app.core.session_manager import SESSION_STORE, SessionRuntime

router = APIRouter(prefix="/session", tags=["session"])

def get_db():
    """Database dependency with proper cleanup"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/check/{vm_id}")
def check_session(
    vm_id: int,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db)
):
    """Check if user has an active session for this VM"""
    vm = db.query(models.VirtualMachine).get(vm_id)
    if not vm:
        raise HTTPException(404, "VM not found")
    
    # Check if VM is locked by current user
    if vm.locked_by == current_user.id and vm.is_busy:
        session = db.query(models.VMSession).filter(
            models.VMSession.vm_id == vm_id,
            models.VMSession.user_id == current_user.id,
            models.VMSession.status == "active"
        ).first()
        
        if session:
            return {
                "has_session": True,
                "session_id": session.id,
                "vm_locked": True
            }
    
    return {
        "has_session": False,
        "session_id": None,
        "vm_locked": vm.is_busy and vm.locked_by == current_user.id
    }

@router.post("/create/{vm_id}")
def create_session(
    vm_id: int,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new VM session"""
    vm = db.query(models.VirtualMachine).get(vm_id)
    if not vm:
        raise HTTPException(404, "VM not found")
    if vm.locked_by != current_user.id:
        raise HTTPException(403, "You do not own this VM")

    session = models.VMSession(vm_id=vm.id, user_id=current_user.id)
    db.add(session)
    db.commit()
    return {"session_id": session.id}

@router.websocket("/ws/{session_id}")
async def session_ws(websocket: WebSocket, session_id: str):
    """WebSocket connection for VM terminal"""
    await websocket.accept()
    
    db = SessionLocal()
    try:
        session = db.query(models.VMSession).get(session_id)
        if not session or session.status != "active":
            await websocket.close()
            return

        vm = db.query(models.VirtualMachine).get(session.vm_id)
        if not vm:
            await websocket.close()
            return

        # Establish SSH connection
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            ssh.connect(
                hostname=vm.host,
                username=vm.username,
                password=vm.password,
                port=22,
                timeout=10
            )
        except Exception as e:
            await websocket.send_text(f"SSH connection failed: {str(e)}")
            await websocket.close()
            return

        channel = ssh.get_transport().open_session()
        channel.get_pty()
        channel.invoke_shell()
        
        SESSION_STORE[session_id] = SessionRuntime(ssh, channel)
        
        # Bidirectional communication
        async def ssh_to_ws():
            try:
                while True:
                    if channel.recv_ready():
                        data = channel.recv(1024).decode()
                        if data:
                            await websocket.send_text(data)
                    await asyncio.sleep(0.05)
            except Exception as e:
                print(f"SSH to WS error: {e}")
        
        async def ws_to_ssh():
            try:
                while True:
                    data = await websocket.receive_text()
                    if data and channel.send_ready():
                        channel.send(data)
            except Exception as e:
                print(f"WS to SSH error: {e}")
        
        await asyncio.gather(ssh_to_ws(), ws_to_ssh())
        
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        # Cleanup resources
        if session_id in SESSION_STORE:
            runtime = SESSION_STORE.pop(session_id)
            if runtime:
                runtime.channel.close()
                runtime.ssh.close()
        
        # Mark session as closed but DON'T release VM
        if session:
            session.status = "closed"
            db.commit()
        
        db.close()