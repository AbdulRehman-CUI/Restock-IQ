from contextlib import asynccontextmanager
import logging
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import datasets, system, ml, decisions, dashboard
from app.core.config import Settings
from app.core.errors import DatasetValidationError, JobConflict, NotFound, StateConflict
from app.db.session import Database
from app.services.jobs import JobWorker

logger = logging.getLogger(__name__)


class UploadLimitMiddleware:
    """Bound the complete multipart body before Starlette parses/spools it."""
    def __init__(self, app, max_bytes):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/api/v1/datasets/import":
            return await self.app(scope, receive, send)
        body = bytearray()
        for key, value in scope["headers"]:
            if key.lower() == b"content-length":
                try:
                    if int(value) > self.max_bytes:
                        return await JSONResponse({"code": "upload_too_large", "message": "Multipart batch exceeds upload limit"}, 413)(scope, receive, send)
                except ValueError:
                    return await JSONResponse({"code": "invalid_content_length", "message": "Invalid content length"}, 400)(scope, receive, send)
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                return await JSONResponse({"code": "upload_too_large", "message": "Multipart batch exceeds upload limit"}, 413)(scope, receive, send)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        await self.app(scope, replay, send)


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        db = Database(settings.runtime_dir / "restockiq.sqlite3")
        db.initialize()
        worker = JobWorker(db, settings)
        worker.recover_interrupted()
        app.state.db, app.state.settings, app.state.worker = db, settings, worker
        try:
            yield
        finally:
            worker.close()
            db.engine.dispose()

    app = FastAPI(title="RestockIQ — Data and Demand Forecasting", version="0.2.0", lifespan=lifespan)
    app.add_middleware(UploadLimitMiddleware, max_bytes=settings.max_upload_bytes)
    app.include_router(system.router)
    app.include_router(datasets.router)
    app.include_router(ml.router)
    app.include_router(decisions.router)
    app.include_router(dashboard.router)

    @app.exception_handler(DatasetValidationError)
    async def dataset_error(request, exc):
        return JSONResponse({"code": "dataset_validation_failed", **exc.issues.payload()}, 422)

    @app.exception_handler(RequestValidationError)
    async def request_error(request, exc):
        errors = [{"file": "request", "row": None, "field": ".".join(map(str, e["loc"])),
                   "code": e["type"], "message": e["msg"]} for e in exc.errors()]
        return JSONResponse({"code": "request_validation_failed", "error_count": len(errors),
                             "errors": errors[:100], "truncated": len(errors) > 100}, 422)

    @app.exception_handler(NotFound)
    async def missing(request, exc):
        return JSONResponse({"code": "not_found", "message": str(exc)}, 404)

    @app.exception_handler(JobConflict)
    async def conflict(request, exc):
        return JSONResponse({"code": "job_conflict", "message": str(exc)}, 409)

    @app.exception_handler(StateConflict)
    async def incompatible_state(request, exc):
        return JSONResponse({"code": "incompatible_state", "message": str(exc)}, 409)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return JSONResponse({"code": "http_error", "message": str(exc.detail)}, exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        error_id = str(uuid4())
        logger.exception("Unexpected request error; error_id=%s", error_id)
        return JSONResponse({"code": "internal_error", "error_id": error_id,
                             "message": "Unexpected server error; check the local log using error_id."}, 500)
    return app


app = create_app()
