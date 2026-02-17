from fastapi import FastAPI
from app.api.health import router as health_router
from app.api.auth import router as auth_router
from app.api.sessions import router as sessions_router
from app.db.database import Base, engine
from fastapi.security import OAuth2PasswordBearer
from fastapi.middleware.cors import CORSMiddleware
# from app.core.security_scheme import oauth2_scheme
from app.api.vm import router as vm_router

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="VM Automation Backend",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# OAuth2PasswordBearer(tokenUrl="/auth/login")
# app.include_router(auth_router)
app.include_router(auth_router)
app.include_router(vm_router)
app.include_router(health_router, prefix="/api")
app.include_router(sessions_router)

@app.get("/")
def root():
    return {"status": "Welcome to the VM Automation Backend API"}