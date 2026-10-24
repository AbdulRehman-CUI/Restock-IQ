from uuid import UUID
from fastapi import APIRouter, Request
from sqlalchemy import text
from app.api.schemas import HealthResponse, JobResponse
from app.core.config import SCHEMA_VERSION, TIMEZONE
from app.db.repositories import get_job

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health(request: Request):
    with request.app.state.db.sessions() as session:
        session.execute(text("SELECT 1"))
    return {"status": "ok", "schema_version": SCHEMA_VERSION, "timezone": TIMEZONE, "reporting_currency": "PKR"}


@router.get("/jobs/{job_id}", response_model=JobResponse)
def job(job_id: UUID, request: Request):
    return get_job(request.app.state.db, job_id)
