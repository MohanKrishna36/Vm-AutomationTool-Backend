from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Text, Float

from sqlalchemy.orm import relationship

from app.db.database import Base

import uuid

from sqlalchemy.dialects.postgresql import UUID

from datetime import datetime





class User(Base):

    __tablename__ = "users"



    id = Column(Integer, primary_key=True, index=True)

    username = Column(String, unique=True, index=True, nullable=False)

    hashed_password = Column(String, nullable=False)



class VirtualMachine(Base):

    __tablename__ = "virtual_machines"



    id = Column(Integer, primary_key=True)

    host = Column(String, unique=True, nullable=False)

    username = Column(String, nullable=False)

    password = Column(String, nullable=False)  # encrypt later

    is_busy = Column(Boolean, default=False)

    locked_by = Column(Integer, ForeignKey("users.id"), nullable=True)



# 👇 THIS MUST BE HERE, TOP LEVEL

class VMSession(Base):

    __tablename__ = "vm_sessions"



    id = Column(Integer, primary_key=True, index=True)

    vm_id = Column(Integer, ForeignKey("virtual_machines.id"), nullable=False)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    status = Column(String, default="active")

    created_at = Column(DateTime, default=datetime.utcnow)


class SessionReport(Base):

    __tablename__ = "session_reports"



    id = Column(Integer, primary_key=True, index=True)

    session_id = Column(String, unique=True, nullable=False, index=True)

    vm_id = Column(Integer, ForeignKey("virtual_machines.id"), nullable=False)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    session_name = Column(String, nullable=True)

    vm_host = Column(String, nullable=False)

    start_time = Column(DateTime, nullable=False)

    end_time = Column(DateTime, nullable=False)

    duration = Column(Integer, nullable=False)  # in milliseconds

    total_commands = Column(Integer, default=0)

    successful_commands = Column(Integer, default=0)

    failed_commands = Column(Integer, default=0)

    success_rate = Column(Float, default=0.0)

    average_execution_time = Column(Float, default=0.0)

    commands_data = Column(Text, nullable=True)  # JSON string of commands array

    generated_at = Column(DateTime, default=datetime.utcnow)

    created_at = Column(DateTime, default=datetime.utcnow)



    # Relationships

    vm = relationship("VirtualMachine", foreign_keys=[vm_id])

    user = relationship("User", foreign_keys=[user_id])


class SavedWorkflow(Base):

    __tablename__ = "saved_workflows"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    steps = Column(Text, nullable=False)   # JSON: [{label, cmd, cat}, ...]
    run_count = Column(Integer, default=0)
    tags = Column(String, nullable=True)   # comma-separated tags

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class AlertRule(Base):
    __tablename__ = "alert_rules"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    vm_id = Column(Integer, ForeignKey("virtual_machines.id"), nullable=False)
    name = Column(String, nullable=False)
    metric = Column(String, nullable=False)    # memory | disk | cpu_load
    operator = Column(String, nullable=False)  # gt | lt | gte | lte
    threshold = Column(Float, nullable=False)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AlertEvent(Base):
    __tablename__ = "alert_events"
    id = Column(Integer, primary_key=True, index=True)
    rule_id = Column(Integer, ForeignKey("alert_rules.id"), nullable=False)
    vm_id = Column(Integer, ForeignKey("virtual_machines.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    rule_name = Column(String, nullable=False)   # denormalized for display
    vm_host = Column(String, nullable=False)
    metric = Column(String, nullable=False)
    operator = Column(String, nullable=False)
    current_value = Column(Float, nullable=False)
    threshold = Column(Float, nullable=False)
    is_active = Column(Boolean, default=True)
    acknowledged = Column(Boolean, default=False)
    triggered_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
    resolved_value = Column(Float, nullable=True)


class CommandHistory(Base):
    __tablename__ = "command_history"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    vm_id = Column(Integer, ForeignKey("virtual_machines.id"), nullable=False)
    vm_host = Column(String, nullable=False)
    command = Column(String, nullable=False)
    category = Column(String, nullable=False)
    action = Column(String, nullable=False)
    output = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    success = Column(Boolean, nullable=False)
    execution_time_ms = Column(Float, nullable=True)
    executed_at = Column(DateTime, default=datetime.utcnow)


class ScheduledJob(Base):

    __tablename__ = "scheduled_jobs"

    id = Column(Integer, primary_key=True, index=True)
    vm_id = Column(Integer, ForeignKey("virtual_machines.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    name = Column(String, nullable=False)
    script = Column(Text, nullable=False)           # command or bash script

    job_type = Column(String, nullable=False)       # "once" | "interval"
    # one-time
    run_at = Column(DateTime, nullable=True)
    # recurring
    interval_value = Column(Integer, nullable=True)
    interval_unit = Column(String, nullable=True)   # minutes | hours | days

    status = Column(String, default="pending")      # pending | running | completed | failed | cancelled
    run_count = Column(Integer, default=0)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    last_output = Column(Text, nullable=True)
    last_success = Column(Boolean, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)



