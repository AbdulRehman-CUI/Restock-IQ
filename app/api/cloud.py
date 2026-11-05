"""Hosted request execution, authenticated workspace, bounded resumable imports."""
import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, delete

from app.data.contracts import CONTRACTS
from app.db.models import Dataset, UploadBatch, UploadChunk
from app.db.repositories import get_summary
from app.services.ingestion import import_dataset, utc_now

router = APIRouter(prefix="/api/v1")
COOKIE = "restockiq_session"


def password():
    return os.environ.get("RESTOCKIQ_ADMIN_PASSWORD", "")


def signed_session():
    expires = str(int(time.time()) + 86400)
    signature = hmac.new(password().encode(), expires.encode(), hashlib.sha256).hexdigest()
    return expires + "." + signature


def authenticated(request):
    token = request.cookies.get(COOKIE, "")
    try:
        expires, signature = token.split(".")
        expected = hmac.new(password().encode(), expires.encode(), hashlib.sha256).hexdigest()
        return bool(password()) and int(expires) > time.time() and hmac.compare_digest(signature, expected)
    except (ValueError, TypeError):
        return False


async def cloud_access(request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        # Same-origin browser mutations; non-browser API clients can omit Origin.
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            return JSONResponse({"message": "Cross-origin requests are not allowed"}, 403)
    if request.url.path.startswith("/api/") and request.url.path not in (
            "/api/v1/health", "/api/v1/session", "/api/v1/login"):
        if not authenticated(request):
            return JSONResponse({"message": "Sign in to access this workspace"}, 401)
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    return response


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=256)


@router.post("/login")
def login(body: Login, request: Request):
    if not password():
        raise HTTPException(503, "Workspace access is not configured")
    if not hmac.compare_digest(body.password.encode(), password().encode()):
        raise HTTPException(401, "Incorrect workspace password")
    response = JSONResponse({"authenticated": True})
    response.set_cookie(COOKIE, signed_session(), httponly=True, secure=request.url.scheme == "https",
                        samesite="strict", max_age=86400)
    return response


@router.get("/session")
def session_status(request: Request):
    return {"authenticated": authenticated(request), "cloud": True}


@router.post("/logout")
def logout():
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(COOKIE)
    return response


@router.post("/jobs/{job_id}/execute")
def execute_job(job_id: UUID, request: Request):
    return request.app.state.worker.execute(str(job_id))


@router.post("/uploads", status_code=201)
def start_upload(request: Request):
    db = request.app.state.db
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with db.transaction() as session:
        session.execute(delete(UploadBatch).where(UploadBatch.created_at < cutoff))
        identifier = str(uuid4())
        session.add(UploadBatch(id=identifier, created_at=utc_now(), total_bytes=0, status="open"))
    return {"id": identifier, "chunk_bytes": 512 * 1024}


@router.put("/uploads/{batch_id}/{filename}/{position}")
async def upload_chunk(batch_id: UUID, filename: str, position: int, request: Request):
    if filename not in CONTRACTS or not 0 <= position < 25:
        raise HTTPException(422, "Unknown CSV contract or invalid chunk position")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 512 * 1024:
            raise HTTPException(413, "Each chunk must be at most 512 KiB")
    db = request.app.state.db
    with db.transaction() as session:
        batch = session.scalar(select(UploadBatch).where(UploadBatch.id == str(batch_id)).with_for_update())
        if batch is None or batch.status != "open":
            raise HTTPException(409, "Upload is missing, expired, or already imported")
        existing = session.get(UploadChunk, (str(batch_id), filename, position))
        if existing:
            if existing.payload != bytes(data):
                raise HTTPException(409, "Chunk position already contains different data")
            return {"received": len(data)}
        if batch.total_bytes + len(data) > request.app.state.settings.max_upload_bytes:
            raise HTTPException(413, "Combined CSV files exceed the 12 MiB upload limit")
        batch.total_bytes += len(data)
        session.add(UploadChunk(batch_id=str(batch_id), filename=filename, position=position, payload=bytes(data)))
    return {"received": len(data)}


class UploadComplete(BaseModel):
    # Client supplies exact sizes so a missing trailing chunk cannot import silently.
    sizes: dict[str, int]


@router.post("/uploads/{batch_id}/complete", status_code=201)
def complete_upload(batch_id: UUID, body: UploadComplete, request: Request):
    db, settings = request.app.state.db, request.app.state.settings
    if set(body.sizes) != set(CONTRACTS) or any(size <= 0 for size in body.sizes.values()):
        raise HTTPException(422, "Provide all six nonempty CSV files")
    identifier = str(batch_id)
    # Transaction-scoped lock also serializes duplicate completion requests.
    with db.transaction() as session:
        batch = session.scalar(select(UploadBatch).where(UploadBatch.id == identifier).with_for_update())
        if batch is None:
            raise HTTPException(404, "Upload not found or expired")
        if session.get(Dataset, identifier):
            return get_summary(db, identifier)
        chunks = list(session.scalars(select(UploadChunk).where(UploadChunk.batch_id == identifier)
                                      .order_by(UploadChunk.filename, UploadChunk.position)))
        with TemporaryDirectory(prefix="restockiq-import-") as temporary:
            for filename in CONTRACTS:
                parts = [c for c in chunks if c.filename == filename]
                if [c.position for c in parts] != list(range(len(parts))) or not parts:
                    raise HTTPException(422, f"Missing chunks for {filename}")
                payload = b"".join(c.payload for c in parts)
                if len(payload) != body.sizes[filename]:
                    raise HTTPException(422, f"Incomplete file: {filename}")
                (Path(temporary) / filename).write_bytes(payload)
            # The dataset UUID makes retries idempotent even after a lost response.
            result = import_dataset(db, settings, Path(temporary), dataset_id=identifier, session_override=session)
        batch.status = "completed"
        session.execute(delete(UploadChunk).where(UploadChunk.batch_id == identifier))
    return result
