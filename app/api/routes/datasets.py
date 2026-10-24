from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app.api.schemas import JobAccepted, QualitySummary
from app.data.contracts import CONTRACTS
from app.data.synthetic import GeneratorConfig
from app.db.repositories import get_summary
from app.services.ingestion import import_dataset

router = APIRouter(prefix="/api/v1/datasets", tags=["datasets"])


@router.post("/demo", status_code=202, response_model=JobAccepted)
def demo(config: GeneratorConfig, request: Request):
    job = request.app.state.worker.submit_demo(config)
    return {"job_id": job["id"], "status_url": f"/api/v1/jobs/{job['id']}"}


@router.post("/import", status_code=201, response_model=QualitySummary)
def import_csvs(request: Request,
                products: UploadFile = File(...), daily_sales: UploadFile = File(...),
                inventory: UploadFile = File(...), supplier_quotes: UploadFile = File(...),
                purchase_orders: UploadFile = File(...), fx_rates: UploadFile = File(...)):
    settings, db = request.app.state.settings, request.app.state.db
    files = (products, daily_sales, inventory, supplier_quotes, purchase_orders, fx_rates)
    temp_root = settings.data_dir / "uploads" / "tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with TemporaryDirectory(prefix="batch-", dir=temp_root) as temporary:
            for filename, upload in zip(CONTRACTS, files):
                # Fixed contract filenames; never trust uploaded path/name components.
                with (Path(temporary) / filename).open("wb") as output:
                    while chunk := upload.file.read(64 * 1024):
                        total += len(chunk)
                        if total > settings.max_upload_bytes:
                            raise HTTPException(413, "CSV batch exceeds configured upload limit")
                        output.write(chunk)
            return import_dataset(db, settings, Path(temporary))
    finally:
        for upload in files:
            upload.file.close()


@router.get("/{dataset_id}/summary", response_model=QualitySummary)
def summary(dataset_id: UUID, request: Request):
    return get_summary(request.app.state.db, dataset_id)
