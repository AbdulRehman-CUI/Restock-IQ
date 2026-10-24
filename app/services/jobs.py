from concurrent.futures import ThreadPoolExecutor
import logging
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.core.errors import JobConflict
from app.data.synthetic import GeneratorConfig, generate_demo
from app.db.models import Job, ModelRun
from app.db.repositories import get_job, get_summary
from app.ml.train import train_model
from app.services.ingestion import import_dataset, utc_now

logger = logging.getLogger(__name__)


class JobWorker:
    """One local worker per API process; database uniqueness rejects duplicate starts."""

    def __init__(self, db, settings):
        self.db, self.settings = db, settings
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="restockiq")

    def recover_interrupted(self):
        with self.db.transaction() as session:
            for job in session.scalars(select(Job).where(Job.status.in_(["queued", "running"]))):
                job.status, job.stage, job.active_key = "failed", "interrupted", None
                job.error = "Application stopped before completion. Submit the demo request again to retry."
                job.updated_at = utc_now()
            for model in session.scalars(select(ModelRun).where(ModelRun.status == "running")):
                model.status = "failed"
                model.error = "Application stopped during training. Submit training again to retry."

    def submit_demo(self, config: GeneratorConfig):
        job_id, now = str(uuid4()), utc_now()
        try:
            with self.db.transaction() as session:
                session.add(Job(id=job_id, status="queued", stage="queued", progress=0,
                                active_key="heavy", created_at=now, updated_at=now,
                                configuration=config.model_dump(mode="json")))
        except IntegrityError as exc:
            raise JobConflict("A heavy job is already queued or running; poll its job ID before starting another") from exc
        try:
            self.executor.submit(self.run_demo, job_id, config)
        except Exception:
            self.update(job_id, status="failed", stage="failed", active_key=None,
                        error="Worker unavailable. Restart the application and retry.")
            raise
        return get_job(self.db, job_id)

    def submit_training(self, dataset_id, seed=42):
        get_summary(self.db, dataset_id)  # Fail synchronously for unknown dataset IDs.
        if not 0 <= seed <= 2**32 - 1:
            raise ValueError("seed must be between 0 and 4294967295")
        job_id, now = str(uuid4()), utc_now()
        try:
            with self.db.transaction() as session:
                session.add(Job(id=job_id, status="queued", stage="queued", progress=0,
                    active_key="heavy", created_at=now, updated_at=now, dataset_id=str(dataset_id),
                    configuration={"task": "training", "dataset_id": str(dataset_id), "seed": seed}))
        except IntegrityError as exc:
            raise JobConflict("A heavy job is already queued or running; retry after it completes") from exc
        try:
            self.executor.submit(self.run_training, job_id, str(dataset_id), seed)
        except Exception:
            self.update(job_id, status="failed", stage="failed", active_key=None,
                        error="Worker unavailable. Restart the application and retry.")
            raise
        return get_job(self.db, job_id)

    def run_training(self, job_id, dataset_id, seed):
        try:
            self.update(job_id, status="running", progress=5, stage="loading_operational_data")
            train_model(self.db, self.settings, dataset_id, seed, job_id=job_id,
                progress=lambda percent, stage: self.update(job_id, progress=percent, stage=stage))
        except Exception:
            error_id = str(uuid4())
            logger.exception("Training job failed; error_id=%s", error_id)
            self.update(job_id, status="failed", stage="failed", active_key=None, error_id=error_id,
                        error="Training failed. Inspect the local log using error_id and retry.")

    def update(self, job_id, **values):
        with self.db.transaction() as session:
            job = session.get(Job, job_id)
            for key, value in values.items():
                setattr(job, key, value)
            job.updated_at = utc_now()

    def run_demo(self, job_id, config):
        try:
            self.update(job_id, status="running", progress=5, stage="simulating")
            generated = generate_demo(self.settings, config,
                progress=lambda percent, stage: self.update(job_id, progress=percent, stage=stage))
            self.update(job_id, progress=80, stage="validating_and_importing")
            # Dataset commit and completed status share a transaction. Restart cannot
            # leave a committed dataset with a job that still claims to be running.
            import_dataset(self.db, self.settings, generated.operational_dir,
                dataset_id=generated.dataset_id, synthetic=True, configuration=config.model_dump(mode="json"),
                completion_job_id=job_id, manifest_path=generated.operational_dir / "manifest.json")
        except Exception:
            error_id = str(uuid4())
            logger.exception("Demo job failed; error_id=%s", error_id)
            self.update(job_id, status="failed", stage="failed", active_key=None, error_id=error_id,
                        error="Demo generation failed. Check the local log using error_id and retry.")

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=False)
