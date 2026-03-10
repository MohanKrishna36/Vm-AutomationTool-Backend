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



