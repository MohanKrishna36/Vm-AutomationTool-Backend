from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Security
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.db import models

router = APIRouter(prefix="/workflows", tags=["workflows"])


class WorkflowStep(BaseModel):
    label: str
    cmd: str
    cat: str


class WorkflowCreate(BaseModel):
    name: str
    description: Optional[str] = None
    steps: List[WorkflowStep]
    tags: Optional[str] = None


class WorkflowResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    steps: str          # raw JSON string (frontend parses)
    run_count: int
    tags: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


@router.post("/", response_model=WorkflowResponse)
def create_workflow(
    payload: WorkflowCreate,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    import json
    wf = models.SavedWorkflow(
        user_id=current_user.id,
        name=payload.name.strip(),
        description=payload.description,
        steps=json.dumps([s.dict() for s in payload.steps]),
        tags=payload.tags,
    )
    db.add(wf)
    db.commit()
    db.refresh(wf)
    return wf


@router.get("/", response_model=List[WorkflowResponse])
def list_workflows(
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(models.SavedWorkflow)
        .filter(models.SavedWorkflow.user_id == current_user.id)
        .order_by(models.SavedWorkflow.updated_at.desc())
        .all()
    )


@router.put("/{wf_id}", response_model=WorkflowResponse)
def update_workflow(
    wf_id: int,
    payload: WorkflowCreate,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    import json
    wf = db.query(models.SavedWorkflow).filter(
        models.SavedWorkflow.id == wf_id,
        models.SavedWorkflow.user_id == current_user.id,
    ).first()
    if not wf:
        raise HTTPException(404, "Workflow not found")

    wf.name = payload.name.strip()
    wf.description = payload.description
    wf.steps = json.dumps([s.dict() for s in payload.steps])
    wf.tags = payload.tags
    wf.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(wf)
    return wf


@router.post("/{wf_id}/run")
def increment_run(
    wf_id: int,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    wf = db.query(models.SavedWorkflow).filter(
        models.SavedWorkflow.id == wf_id,
        models.SavedWorkflow.user_id == current_user.id,
    ).first()
    if not wf:
        raise HTTPException(404, "Workflow not found")
    wf.run_count = (wf.run_count or 0) + 1
    wf.updated_at = datetime.utcnow()
    db.commit()
    return {"run_count": wf.run_count}


@router.delete("/{wf_id}")
def delete_workflow(
    wf_id: int,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    wf = db.query(models.SavedWorkflow).filter(
        models.SavedWorkflow.id == wf_id,
        models.SavedWorkflow.user_id == current_user.id,
    ).first()
    if not wf:
        raise HTTPException(404, "Workflow not found")
    db.delete(wf)
    db.commit()
    return {"message": "Deleted"}
