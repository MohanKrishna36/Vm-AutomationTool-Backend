from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime
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

