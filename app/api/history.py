from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Security
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.db import models

router = APIRouter(prefix="/history", tags=["history"])


class HistoryEntry(BaseModel):
    id: int
    vm_id: int
    vm_host: str
    command: str
    category: str
    action: str
    output: Optional[str]
    error: Optional[str]
    success: bool
    execution_time_ms: Optional[float]
    executed_at: datetime

    class Config:
        from_attributes = True


class HistorySave(BaseModel):
    vm_id: int
    vm_host: str
    command: str
    category: str = "raw"
    action: str = "broadcast"
    output: Optional[str] = None
    error: Optional[str] = None
    success: bool
    execution_time_ms: Optional[float] = None


@router.post("/save")
def save_history_entry(
    entry: HistorySave,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    row = models.CommandHistory(
        user_id=current_user.id,
        vm_id=entry.vm_id,
        vm_host=entry.vm_host,
        command=entry.command,
        category=entry.category,
        action=entry.action,
        output=entry.output,
        error=entry.error,
        success=entry.success,
        execution_time_ms=entry.execution_time_ms,
        executed_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    return {"ok": True}


@router.get("/", response_model=List[HistoryEntry])
def get_history(
    vm_id: Optional[int] = Query(None),
    success: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(models.CommandHistory).filter(
        models.CommandHistory.user_id == current_user.id
    )
    if vm_id is not None:
        q = q.filter(models.CommandHistory.vm_id == vm_id)
    if success is not None:
        q = q.filter(models.CommandHistory.success == success)
    if category:
        q = q.filter(models.CommandHistory.category == category)
    if search:
        q = q.filter(models.CommandHistory.command.ilike(f"%{search}%"))
    return q.order_by(models.CommandHistory.executed_at.desc()).offset(offset).limit(limit).all()


@router.get("/stats")
def history_stats(
    vm_id: Optional[int] = Query(None),
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(models.CommandHistory).filter(
        models.CommandHistory.user_id == current_user.id
    )
    if vm_id is not None:
        q = q.filter(models.CommandHistory.vm_id == vm_id)
    total = q.count()
    succeeded = q.filter(models.CommandHistory.success == True).count()
    return {
        "total": total,
        "succeeded": succeeded,
        "failed": total - succeeded,
        "success_rate": round(succeeded / total * 100, 1) if total else 0,
    }


@router.delete("/clear")
def clear_history(
    vm_id: Optional[int] = Query(None),
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(models.CommandHistory).filter(
        models.CommandHistory.user_id == current_user.id
    )
    if vm_id is not None:
        q = q.filter(models.CommandHistory.vm_id == vm_id)
    q.delete()
    db.commit()
    return {"message": "History cleared"}


@router.delete("/{entry_id}")
def delete_entry(
    entry_id: int,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    entry = db.query(models.CommandHistory).filter(
        models.CommandHistory.id == entry_id,
        models.CommandHistory.user_id == current_user.id,
    ).first()
    if not entry:
        raise HTTPException(404, "Entry not found")
    db.delete(entry)
    db.commit()
    return {"message": "Deleted"}
