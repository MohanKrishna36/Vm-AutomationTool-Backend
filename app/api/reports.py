from fastapi import APIRouter, Depends, HTTPException, Security
from sqlalchemy.orm import Session
from typing import List, Optional
from app.db.database import SessionLocal
from app.db import models
from app.core.deps import get_current_user
from pydantic import BaseModel
from datetime import datetime
import json

router = APIRouter(prefix="/reports", tags=["reports"])

def get_db():
    """Database dependency with proper cleanup"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Pydantic models for request/response
class CommandData(BaseModel):
    command: str
    timestamp: int
    executionTime: int
    success: bool

class ReportCreate(BaseModel):
    session_id: str
    vm_id: int
    session_name: Optional[str] = None
    vm_host: str
    start_time: datetime
    end_time: datetime
    duration: int
    total_commands: int
    successful_commands: int
    failed_commands: int
    success_rate: float
    average_execution_time: float
    commands: List[CommandData]

class ReportResponse(BaseModel):
    id: int
    session_id: str
    vm_id: int
    session_name: Optional[str]
    vm_host: str
    start_time: datetime
    end_time: datetime
    duration: int
    total_commands: int
    successful_commands: int
    failed_commands: int
    success_rate: float
    average_execution_time: float
    commands_data: Optional[str]
    generated_at: datetime
    created_at: datetime

    class Config:
        from_attributes = True

@router.post("/", response_model=ReportResponse)
def create_report(
    report: ReportCreate,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new session report"""
    
    # Verify VM exists and user has access
    vm = db.query(models.VirtualMachine).filter(
        models.VirtualMachine.id == report.vm_id,
        models.VirtualMachine.locked_by == current_user.id
    ).first()
    
    if not vm:
        raise HTTPException(404, "VM not found or access denied")
    
    # Check if report already exists for this session
    existing_report = db.query(models.SessionReport).filter(
        models.SessionReport.session_id == report.session_id
    ).first()
    
    if existing_report:
        raise HTTPException(400, "Report already exists for this session")
    
    # Convert commands to JSON string
    commands_json = json.dumps([cmd.dict() for cmd in report.commands])
    
    # Create report
    db_report = models.SessionReport(
        session_id=report.session_id,
        vm_id=report.vm_id,
        user_id=current_user.id,
        session_name=report.session_name,
        vm_host=report.vm_host,
        start_time=report.start_time,
        end_time=report.end_time,
        duration=report.duration,
        total_commands=report.total_commands,
        successful_commands=report.successful_commands,
        failed_commands=report.failed_commands,
        success_rate=report.success_rate,
        average_execution_time=report.average_execution_time,
        commands_data=commands_json,
        generated_at=datetime.utcnow()
    )
    
    db.add(db_report)
    db.commit()
    db.refresh(db_report)
    
    return db_report

@router.get("/", response_model=List[ReportResponse])
def get_user_reports(
    current_user=Security(get_current_user),
    db: Session = Depends(get_db),
    limit: int = 100,
    offset: int = 0
):
    """Get all reports for the current user"""
    
    reports = db.query(models.SessionReport).filter(
        models.SessionReport.user_id == current_user.id
    ).order_by(
        models.SessionReport.created_at.desc()
    ).offset(offset).limit(limit).all()
    
    return reports

@router.get("/{session_id}", response_model=ReportResponse)
def get_report(
    session_id: str,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db)
):
    """Get a specific report by session ID"""
    
    report = db.query(models.SessionReport).filter(
        models.SessionReport.session_id == session_id,
        models.SessionReport.user_id == current_user.id
    ).first()
    
    if not report:
        raise HTTPException(404, "Report not found")
    
    return report

@router.delete("/{session_id}")
def delete_report(
    session_id: str,
    current_user=Security(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a specific report"""
    
    report = db.query(models.SessionReport).filter(
        models.SessionReport.session_id == session_id,
        models.SessionReport.user_id == current_user.id
    ).first()
    
    if not report:
        raise HTTPException(404, "Report not found")
    
    db.delete(report)
    db.commit()
    
    return {"message": "Report deleted successfully"}

@router.get("/stats/summary")
def get_reports_summary(
    current_user=Security(get_current_user),
    db: Session = Depends(get_db)
):
    """Get summary statistics for user's reports"""
    
    total_reports = db.query(models.SessionReport).filter(
        models.SessionReport.user_id == current_user.id
    ).count()
    
    if total_reports == 0:
        return {
            "total_reports": 0,
            "avg_success_rate": 0,
            "total_commands": 0,
            "total_duration": 0
        }
    
    # Calculate averages
    reports = db.query(models.SessionReport).filter(
        models.SessionReport.user_id == current_user.id
    ).all()
    
    avg_success_rate = sum(r.success_rate for r in reports) / len(reports)
    total_commands = sum(r.total_commands for r in reports)
    total_duration = sum(r.duration for r in reports)
    
    return {
        "total_reports": total_reports,
        "avg_success_rate": round(avg_success_rate, 2),
        "total_commands": total_commands,
        "total_duration": total_duration
    }
