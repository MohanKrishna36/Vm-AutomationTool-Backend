from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.api.health import router as health_router
from app.api.auth import router as auth_router
from app.api.sessions import router as sessions_router
from app.api.reports import router as reports_router
from app.api.jobs import router as jobs_router
from app.api.workflows import router as workflows_router
from app.api.alerts import router as alerts_router
from app.api.history import router as history_router
from app.db.database import Base, engine
from fastapi.security import OAuth2PasswordBearer
from fastapi.middleware.cors import CORSMiddleware
from app.api.vm import router as vm_router
from app.api.dispatcher import router as dispatcher_router
from app.core.job_runner import start_scheduler, scheduler


Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)



app = FastAPI(
    title="VM Automation Backend",
    version="1.0.0",
    lifespan=lifespan,
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

app.include_router(reports_router)
app.include_router(dispatcher_router)
app.include_router(jobs_router)
app.include_router(workflows_router)
app.include_router(alerts_router)
app.include_router(history_router)



@app.get("/")

def root():

    return {"status": "Welcome to the VM Automation Backend API"}