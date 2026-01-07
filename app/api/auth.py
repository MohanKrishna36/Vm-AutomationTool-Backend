from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.db import models
from app.core.security import hash_password, verify_password, create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


class UserCredentials(BaseModel):
    username: str
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str):
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 bytes")
        return v


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/signup", status_code=201, operation_id="auth_signup")
def signup(data: UserCredentials, db: Session = Depends(get_db)):
    if db.query(models.User).filter(models.User.username == data.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")

    user = models.User(
        username=data.username,
        hashed_password=hash_password(data.password)
    )
    db.add(user)
    db.commit()

    return {"message": "User created"}


@router.post("/login")
def login(data: UserCredentials, db: Session = Depends(get_db), operation_id="auth_login"):
    user = db.query(models.User).filter(models.User.username == data.username).first()

    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )

    token = create_access_token({"sub": user.username})
    return {
        "access_token": token,
        "token_type": "bearer"
    }
