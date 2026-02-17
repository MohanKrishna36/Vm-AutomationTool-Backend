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
            "locked_by": vm.locked_by 
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
