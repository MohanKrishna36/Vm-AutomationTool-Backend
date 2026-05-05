from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Security
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.core.job_runner import cancel_job, schedule_job, trigger_now
from app.db import models

router = APIRouter(prefix="/jobs", tags=["jobs"])


# ── Pydantic ──────────────────────────────────────────────────────────────────

class JobCreate(BaseModel):
    vm_id: int
    name: str
    script: str
    job_type: str                         # "once" | "interval"
    run_at: Optional[datetime] = None     # for once
    interval_value: Optional[int] = None  # for interval
    interval_unit: Optional[str] = None   # minutes | hours | days


class JobResponse(BaseModel):
    id: int
    vm_id: int
    name: str
    script: str
    job_type: str
    run_at: Optional[datetime]
    interval_value: Optional[int]
    interval_unit: Optional[str]
    status: str
    run_count: int
    last_run_at: Optional[datetime]
    next_run_at: Optional[datetime]
    last_output: Optional[str]
    last_success: Optional[bool]
    created_at: datetime

    class Config:
        from_attributes = True


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/", response_model=JobResponse)
def create_job(
    payload: JobCreate,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    vm = db.query(models.VirtualMachine).get(payload.vm_id)
    if not vm:
        raise HTTPException(404, "VM not found")

    if payload.job_type == "once":
        if not payload.run_at:
            raise HTTPException(400, "run_at required for one-time jobs")
    elif payload.job_type == "interval":
        if not payload.interval_value or not payload.interval_unit:
            raise HTTPException(400, "interval_value and interval_unit required")
        if payload.interval_unit not in ("minutes", "hours", "days"):
            raise HTTPException(400, "interval_unit must be minutes, hours, or days")
    else:
        raise HTTPException(400, "job_type must be 'once' or 'interval'")

    job = models.ScheduledJob(
        vm_id=payload.vm_id,
        user_id=current_user.id,
        name=payload.name,
        script=payload.script,
        job_type=payload.job_type,
        run_at=payload.run_at,
        interval_value=payload.interval_value,
        interval_unit=payload.interval_unit,
        status="pending",
        run_count=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    schedule_job(job)
    db.refresh(job)  # pick up next_run_at written by schedule_job
    return job


@router.get("/vm/{vm_id}", response_model=List[JobResponse])
def list_jobs(
    vm_id: int,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(models.ScheduledJob)
        .filter(
            models.ScheduledJob.vm_id == vm_id,
            models.ScheduledJob.user_id == current_user.id,
        )
        .order_by(models.ScheduledJob.created_at.desc())
        .all()
    )


@router.post("/{job_id}/run", response_model=dict)
def run_now(
    job_id: int,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    job = db.query(models.ScheduledJob).filter(
        models.ScheduledJob.id == job_id,
        models.ScheduledJob.user_id == current_user.id,
    ).first()
    if not job:
        raise HTTPException(404, "Job not found")
    trigger_now(job_id)
    return {"message": "Job triggered"}


@router.delete("/{job_id}")
def delete_job(
    job_id: int,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    job = db.query(models.ScheduledJob).filter(
        models.ScheduledJob.id == job_id,
        models.ScheduledJob.user_id == current_user.id,
    ).first()
    if not job:
        raise HTTPException(404, "Job not found")
    cancel_job(job_id)
    db.delete(job)
    db.commit()
    return {"message": "Job deleted"}


@router.patch("/{job_id}/cancel")
def cancel_job_endpoint(
    job_id: int,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    job = db.query(models.ScheduledJob).filter(
        models.ScheduledJob.id == job_id,
        models.ScheduledJob.user_id == current_user.id,
    ).first()
    if not job:
        raise HTTPException(404, "Job not found")
    cancel_job(job_id)
    job.status = "cancelled"
    db.commit()
    return {"message": "Job cancelled"}
