from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from app.core.config import SCHEMA_VERSION, TIMEZONE
from app.db.repositories import add_dataset
from app.db.models import Job
from app.services.quality import quality_summary
from app.services.validation import read_batch


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def make_manifest(batch, quality, dataset_id, provenance="uploaded", configuration=None):
    return {"dataset_id": dataset_id, "schema_version": SCHEMA_VERSION,
            "provenance": provenance,
            "synthetic_notice": "All business records and FX rates are fictional synthetic data." if provenance == "synthetic" else None,
            "seed": configuration.get("seed") if configuration else None,
            "generator_configuration": configuration,
            "start_date": batch.start_date.isoformat(), "end_date": batch.end_date.isoformat(),
            "timezone": TIMEZONE, "reporting_currency": "PKR", "row_counts": quality["row_counts"],
            "created_at": utc_now(), "file_hashes": batch.hashes,
            "validation_summary": quality}


def import_dataset(db, settings, directory: Path, *, dataset_id=None, configuration=None,
                   synthetic=False, completion_job_id=None, manifest_path=None):
    batch = read_batch(Path(directory))
    quality = quality_summary(batch, settings)
    dataset_id = dataset_id or str(uuid4())
    manifest = make_manifest(batch, quality, dataset_id, "synthetic" if synthetic else "uploaded", configuration)
    with db.transaction() as session:
        add_dataset(session, dataset_id, batch, manifest, quality)
        if completion_job_id:
            job = session.get(Job, completion_job_id)
            job.status, job.stage, job.progress = "completed", "completed", 100
            job.dataset_id, job.active_key, job.updated_at = dataset_id, None, utc_now()
        if manifest_path:
            write_manifest(manifest_path, manifest)
    return {"dataset_id": dataset_id, "manifest": manifest, **quality}


def write_manifest(path, manifest):
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
