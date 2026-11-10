"""Hosted request execution and browser response policy."""
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/v1")


async def cloud_headers(request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        # Same-origin browser mutations; non-browser API clients can omit Origin.
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            return JSONResponse({"message": "Cross-origin requests are not allowed"}, 403)
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@router.post("/jobs/{job_id}/execute")
def execute_job(job_id: UUID, request: Request):
    return request.app.state.worker.execute(str(job_id))
