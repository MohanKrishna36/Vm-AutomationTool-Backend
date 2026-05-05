from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Security
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.db import models

router = APIRouter(prefix="/alerts", tags=["alerts"])

VALID_METRICS = {"memory", "disk", "cpu_load"}
VALID_OPS = {"gt", "lt", "gte", "lte"}


# ── Pydantic models ───────────────────────────────────────────────────────────

class RuleCreate(BaseModel):
    vm_id: int
    name: str
    metric: str
    operator: str
    threshold: float


class RuleResponse(BaseModel):
    id: int
    vm_id: int
    name: str
    metric: str
    operator: str
    threshold: float
    enabled: bool
    created_at: datetime

    class Config:
        from_attributes = True


class EventResponse(BaseModel):
    id: int
    rule_id: int
    vm_id: int
    rule_name: str
    vm_host: str
    metric: str
    operator: str
    current_value: float
    threshold: float
    is_active: bool
    acknowledged: bool
    triggered_at: datetime
    resolved_at: Optional[datetime]
    resolved_value: Optional[float]

    class Config:
        from_attributes = True


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/count")
def alert_count(
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    count = db.query(models.AlertEvent).filter(
        models.AlertEvent.user_id == current_user.id,
        models.AlertEvent.is_active == True,
        models.AlertEvent.acknowledged == False,
    ).count()
    return {"count": count}


@router.get("/rules", response_model=List[RuleResponse])
def list_rules(
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(models.AlertRule)
        .filter(models.AlertRule.user_id == current_user.id)
        .order_by(models.AlertRule.created_at.desc())
        .all()
    )


@router.post("/rules", response_model=RuleResponse)
def create_rule(
    payload: RuleCreate,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.metric not in VALID_METRICS:
        raise HTTPException(400, f"metric must be one of: {', '.join(sorted(VALID_METRICS))}")
    if payload.operator not in VALID_OPS:
        raise HTTPException(400, f"operator must be one of: {', '.join(sorted(VALID_OPS))}")
    if not db.query(models.VirtualMachine).get(payload.vm_id):
        raise HTTPException(404, "VM not found")

    rule = models.AlertRule(
        user_id=current_user.id,
        vm_id=payload.vm_id,
        name=payload.name.strip(),
        metric=payload.metric,
        operator=payload.operator,
        threshold=payload.threshold,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.patch("/rules/{rule_id}/toggle")
def toggle_rule(
    rule_id: int,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    rule = db.query(models.AlertRule).filter(
        models.AlertRule.id == rule_id,
        models.AlertRule.user_id == current_user.id,
    ).first()
    if not rule:
        raise HTTPException(404, "Rule not found")
    rule.enabled = not rule.enabled
    db.commit()
    return {"enabled": rule.enabled}


@router.delete("/rules/{rule_id}")
def delete_rule(
    rule_id: int,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    rule = db.query(models.AlertRule).filter(
        models.AlertRule.id == rule_id,
        models.AlertRule.user_id == current_user.id,
    ).first()
    if not rule:
        raise HTTPException(404, "Rule not found")
    db.query(models.AlertEvent).filter(models.AlertEvent.rule_id == rule_id).delete()
    db.delete(rule)
    db.commit()
    return {"message": "Deleted"}


@router.get("/events", response_model=List[EventResponse])
def list_events(
    active_only: bool = True,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(models.AlertEvent).filter(
        models.AlertEvent.user_id == current_user.id
    )
    if active_only:
        q = q.filter(models.AlertEvent.is_active == True)
    return q.order_by(models.AlertEvent.triggered_at.desc()).limit(50).all()


@router.post("/events/{event_id}/acknowledge")
def acknowledge(
    event_id: int,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
):
    ev = db.query(models.AlertEvent).filter(
        models.AlertEvent.id == event_id,
        models.AlertEvent.user_id == current_user.id,
    ).first()
    if not ev:
        raise HTTPException(404, "Event not found")
    ev.acknowledged = True
    db.commit()
    return {"acknowledged": True}
