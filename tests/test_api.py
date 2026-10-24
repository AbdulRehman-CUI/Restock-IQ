import threading
import time
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from app.api.main import create_app
from app.core.config import Settings
from app.data.contracts import CONTRACTS
from app.db.models import Job
from app.services.ingestion import utc_now
from conftest import mutate_csv


def uploads(directory):
    return {name.removesuffix(".csv"): ("../../untrusted.csv", (directory / name).read_bytes(), "text/csv")
            for name in CONTRACTS}


def wait_job(client, job_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in ("failed", "completed"):
            return job
        time.sleep(0.02)
    pytest.fail("Demo job did not finish within 20 seconds")


def test_health_missing_ids_and_bad_requests(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/health").json()["timezone"] == "Asia/Karachi"
        assert client.get(f"/api/v1/jobs/{uuid4()}").status_code == 404
        assert client.get(f"/api/v1/datasets/{uuid4()}/summary").status_code == 404
        assert client.get("/api/v1/jobs/not-a-uuid").status_code == 422
        assert client.post("/api/v1/datasets/demo", json={"days": 0}).status_code == 422
        assert client.post("/api/v1/datasets/import").status_code == 422


def test_demo_job_progress_conflict_and_persistence(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        original = app.state.worker.run_demo
        gate, started = threading.Event(), threading.Event()
        def controlled(job_id, config):
            started.set()
            gate.wait(timeout=5)
            original(job_id, config)
        app.state.worker.run_demo = controlled
        accepted = client.post("/api/v1/datasets/demo", json={"days": 40, "sku_count": 5})
        assert accepted.status_code == 202
        job_id = accepted.json()["job_id"]
        assert started.wait(timeout=5)
        try:
            status = client.get(accepted.json()["status_url"]).json()
            assert status["status"] == "queued" and status["progress"] == 0
            assert client.post("/api/v1/datasets/demo", json={}).status_code == 409
        finally:
            gate.set()
        job = wait_job(client, job_id)
        assert job["status"] == "completed", job
        assert job["progress"] == 100 and job["dataset_id"]
        result = client.get(f"/api/v1/datasets/{job['dataset_id']}/summary")
        assert result.status_code == 200
        assert result.json()["row_counts"]["daily_sales"] == 200
        assert result.json()["manifest"]["provenance"] == "synthetic"
        assert "latent_demand" not in result.text
        assert "evaluation_only" not in result.text
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/api/v1/jobs/{job_id}").json()["status"] == "completed"


def test_failure_and_interrupted_recovery(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        with patch("app.services.jobs.generate_demo", side_effect=RuntimeError("SECRET internal path")):
            response = client.post("/api/v1/datasets/demo", json={"days": 2, "sku_count": 1})
            failed = wait_job(client, response.json()["job_id"])
        assert failed["status"] == "failed" and failed["error_id"]
        assert "SECRET" not in failed["error"]
        interrupted_id = str(uuid4())
        with app.state.db.transaction() as session:
            session.add(Job(id=interrupted_id, status="running", stage="simulating", progress=30,
                            active_key="demo", created_at=utc_now(), updated_at=utc_now(), configuration={}))
    with TestClient(create_app(settings)) as client:
        job = client.get(f"/api/v1/jobs/{interrupted_id}").json()
        assert job["status"] == "failed" and job["stage"] == "interrupted"
        assert "retry" in job["error"]
        assert client.post("/api/v1/datasets/demo", json={"days": 2, "sku_count": 1}).status_code == 202


def test_multipart_import_success_failure_and_cleanup(settings, batch_dir):
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        result = client.post("/api/v1/datasets/import", files=uploads(batch_dir))
        assert result.status_code == 201, result.text
        assert result.json()["row_counts"]["daily_sales"] == 300
        assert result.json()["manifest"]["provenance"] == "uploaded"
        mutate_csv(batch_dir, "daily_sales.csv", lambda rows: rows[0].update(sku="unknown"))
        invalid = client.post("/api/v1/datasets/import", files=uploads(batch_dir))
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "dataset_validation_failed"
        assert invalid.json()["errors"][0]["file"] == "daily_sales.csv"
        assert list((settings.data_dir / "uploads" / "tmp").iterdir()) == []
        assert not (settings.data_dir / "untrusted.csv").exists()
        with patch("app.api.routes.datasets.import_dataset", side_effect=RuntimeError("SECRET traceback")):
            unexpected = client.post("/api/v1/datasets/import", files=uploads(batch_dir))
            assert unexpected.status_code == 500
            assert unexpected.json()["error_id"]
            assert "SECRET" not in unexpected.text


def test_upload_limit_with_and_without_content_length(tmp_path, batch_dir):
    settings = Settings(data_dir=tmp_path / "data", runtime_dir=tmp_path / "runtime", max_upload_bytes=500)
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/v1/datasets/import", files=uploads(batch_dir)).status_code == 413
        response = client.post("/api/v1/datasets/import", content=iter([b"x" * 300, b"x" * 300]),
                               headers={"Content-Type": "multipart/form-data; boundary=test"})
        assert response.status_code == 413
