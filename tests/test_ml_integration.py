import builtins
from datetime import date
import json
from pathlib import Path
import threading
import time
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, inspect, select

from app.api.main import create_app
from app.core.config import Settings
from app.core.errors import ArtifactError, StateConflict
from app.data.synthetic import GeneratorConfig, generate_demo
from app.db.models import Base, Dataset, DailySale, Job, ModelRun
from app.db.repositories import get_summary, load_demand_data, model_record
from app.db.session import Database
from app.ml.artifacts import artifact_directory
from app.ml.train import train_model
from app.services.forecasting import create_forecast, csv_text, export_forecast
from app.services.ingestion import import_dataset, utc_now


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    root = tmp_path_factory.mktemp("ml-integration")
    settings = Settings(data_dir=root / "data", runtime_dir=root / "runtime", artifact_dir=root / "artifacts")
    generated = generate_demo(settings, GeneratorConfig(days=120, sku_count=2))
    db = Database(settings.runtime_dir / "restockiq.sqlite3")
    db.initialize()
    summary = import_dataset(db, settings, generated.operational_dir, synthetic=True,
                             configuration=generated.manifest["generator_configuration"])
    # Reads of hidden truth are forbidden during all ordinary model operations.
    original_open, original_path_open = builtins.open, Path.open
    def guarded_open(file, *args, **kwargs):
        assert "evaluation_only" not in str(file)
        return original_open(file, *args, **kwargs)
    def guarded_path_open(path, *args, **kwargs):
        assert "evaluation_only" not in str(path)
        return original_path_open(path, *args, **kwargs)
    with patch("builtins.open", guarded_open), patch.object(Path, "open", guarded_path_open):
        model = train_model(db, settings, summary["dataset_id"])
        forecast = create_forecast(db, settings, summary["dataset_id"], model["id"], date.fromisoformat(summary["date_range"]["end"]), 30)
    yield db, settings, summary, model, forecast
    db.engine.dispose()


def test_training_lifecycle_forecast_and_export(trained, tmp_path):
    db, settings, summary, model, forecast = trained
    assert model["status"] == "completed" and model["role"] == "final_inference"
    assert len(model["details"]["evaluation_model_ids"]) == 2
    for partition, identifier in model["details"]["evaluation_model_ids"].items():
        evaluation = model_record(db, identifier)
        assert evaluation["role"] == "evaluation"
        expected = model["details"]["partitions"]["train" if partition == "validation" else "validation"]["end"]
        assert evaluation["training_cutoff"].isoformat() == expected
        assert evaluation["parent_model_id"] == model["id"]
        assert evaluation["selected_method"] == "random_forest"  # Fixed comparator, never future validation selection.
    assert model["details"]["selection"]["selected_method"] == model["selected_method"]
    assert forecast["row_count"] == 60 and forecast["forecasted_products"] == 2
    assert all(r["dataset_id"] == summary["dataset_id"] for r in forecast["rows"])
    assert all(r["expected_units"] >= 0 for r in forecast["rows"])
    output = tmp_path / "forecast.csv"
    exported = export_forecast(db, forecast["forecast_run_id"], output)
    assert exported["row_count"] == len(output.read_text().splitlines()) - 1 == 60
    metadata = json.loads(output.with_suffix(".csv.metadata.json").read_text())
    assert len(metadata["product_status"]) == 2
    with pytest.raises(FileExistsError):
        export_forecast(db, forecast["forecast_run_id"], output)
    with pytest.raises(StateConflict):
        create_forecast(db, settings, summary["dataset_id"], model["id"], model["training_cutoff"].replace(day=1), 7)
    with pytest.raises(StateConflict):
        create_forecast(db, settings, str(uuid4()), model["id"], model["training_cutoff"], 7)


def test_failed_artifact_write_is_not_completed(db, settings, small_demo):
    summary = import_dataset(db, settings, small_demo.operational_dir)
    with patch("app.ml.train.save_artifact", side_effect=OSError("simulated disk failure")), pytest.raises(OSError):
        train_model(db, settings, summary["dataset_id"])
    with db.sessions() as session:
        runs = list(session.scalars(select(ModelRun)))
        assert len(runs) == 1
        assert runs[0].status == "failed" and runs[0].error_id
        assert runs[0].artifact_hash is None
        assert session.scalar(select(func.count()).select_from(Dataset)) == 1


def test_additive_schema_extension_preserves_phase1(settings, small_demo):
    db = Database(settings.runtime_dir / "legacy.sqlite3")
    original_tables = [table for table in Base.metadata.sorted_tables if table.name not in ("model_runs", "forecast_runs", "forecast_rows")]
    Base.metadata.create_all(db.engine, tables=original_tables)
    summary = import_dataset(db, settings, small_demo.operational_dir)
    assert "model_runs" not in inspect(db.engine).get_table_names()
    db.initialize()
    assert "model_runs" in inspect(db.engine).get_table_names()
    assert get_summary(db, summary["dataset_id"]) == summary
    with db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(DailySale)) == 300
    db.engine.dispose()


def poll(client, job_id):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in ("failed", "completed"):
            return job
        time.sleep(0.02)
    pytest.fail("Training job did not finish")


def test_training_api_jobs_requests_and_failures(settings, small_demo):
    app = create_app(settings)
    with TestClient(app) as client:
        summary = import_dataset(app.state.db, settings, small_demo.operational_dir)
        dataset_id = summary["dataset_id"]
        assert client.post("/api/v1/models/train", json={"dataset_id": str(uuid4())}).status_code == 404
        assert client.post("/api/v1/models/train", json={"dataset_id": dataset_id, "seed": -1}).status_code == 422
        gate, started = threading.Event(), threading.Event()
        original = app.state.worker.run_training
        def controlled(*args):
            started.set()
            gate.wait(timeout=5)
            original(*args)
        app.state.worker.run_training = controlled
        response = client.post("/api/v1/models/train", json={"dataset_id": dataset_id})
        assert response.status_code == 202
        assert started.wait(timeout=5)
        try:
            assert client.post("/api/v1/models/train", json={"dataset_id": dataset_id}).status_code == 409
            assert client.post("/api/v1/datasets/demo", json={}).status_code == 409
        finally:
            gate.set()
        job = poll(client, response.json()["job_id"])
        assert job["status"] == "completed", job
        model_id = job["model_id"]
        metrics = client.get(f"/api/v1/models/{model_id}/metrics")
        assert metrics.status_code == 200 and metrics.json()["status"] == "completed"
        body = {"dataset_id": dataset_id, "model_id": model_id, "origin": summary["date_range"]["end"], "horizon_days": 30}
        predicted = client.post("/api/v1/forecasts", json=body)
        assert predicted.status_code == 201, predicted.text
        assert predicted.json()["row_count"] == 150
        run_id = predicted.json()["forecast_run_id"]
        assert client.get(f"/api/v1/forecasts/{run_id}").json() == predicted.json()
        export = client.get(f"/api/v1/forecasts/{run_id}/export")
        assert export.status_code == 200 and len(export.text.splitlines()) == 151
        assert client.post("/api/v1/forecasts", json={**body, "horizon_days": 31}).status_code == 422
        assert client.post("/api/v1/forecasts", json={**body, "origin": "2025-10-17"}).status_code == 409
        assert client.post("/api/v1/forecasts", json={**body, "dataset_id": str(uuid4())}).status_code == 409
        assert client.get(f"/api/v1/models/{uuid4()}/metrics").status_code == 404
        pipeline = artifact_directory(settings, model_id) / "pipeline.joblib"
        pipeline.unlink()
        assert client.post("/api/v1/forecasts", json=body).status_code == 409
        app.state.worker.run_training = original
        with patch("app.ml.train.save_artifact", side_effect=OSError("SECRET failure")):
            failure = client.post("/api/v1/models/train", json={"dataset_id": dataset_id})
            failed = poll(client, failure.json()["job_id"])
        assert failed["status"] == "failed" and failed["model_id"]
        assert "SECRET" not in failed["error"]
        assert client.get(f"/api/v1/models/{failed['model_id']}/metrics").json()["status"] == "failed"


def test_export_escapes_untrusted_sku(trained):
    _, _, _, _, result = trained
    changed = {**result, "rows": [{**result["rows"][0], "sku": "=SUM(A1:A2)"}]}
    assert "'=SUM(A1:A2)" in csv_text(changed)


def test_export_failure_keeps_existing_metadata_and_removes_new_partial_files(trained, tmp_path):
    db, _, _, _, forecast = trained
    output = tmp_path / "untouched.csv"
    sidecar = output.with_suffix(".csv.metadata.json")
    sidecar.write_text("keep me")
    with pytest.raises(FileExistsError):
        export_forecast(db, forecast["forecast_run_id"], output)
    assert not output.exists() and sidecar.read_text() == "keep me"
    output = tmp_path / "new.csv"
    with patch("app.services.forecasting.json.dump", side_effect=OSError("write failed")), pytest.raises(OSError):
        export_forecast(db, forecast["forecast_run_id"], output)
    assert not output.exists() and not output.with_suffix(".csv.metadata.json").exists()


def test_test_metrics_cannot_override_validation_selection(db, settings, small_demo):
    summary = import_dataset(db, settings, small_demo.operational_dir)
    def evaluation(base_mae, rf_mae):
        return {"7": {"methods": {"baseline": {"count": 100, "mae_units": base_mae},
                                   "random_forest": {"count": 100, "mae_units": rf_mae}},
                       "counts": {"paired_origin_count": 4}}}
    # Deliberately opposite rankings prove that the lifecycle does not reselect
    # from the test partition. These are isolated test doubles, not demo metrics.
    with patch("app.ml.train.evaluate_partition", side_effect=[evaluation(5, 2), evaluation(1, 100)]):
        run = train_model(db, settings, summary["dataset_id"])
    assert run["selected_method"] == "random_forest"
    assert run["details"]["selection"]["baseline_mae_units"] == 5
    assert run["details"]["test"]["7"]["methods"]["baseline"]["mae_units"] == 1


def test_restart_marks_interrupted_training_and_model_failed(settings, small_demo):
    app = create_app(settings)
    job_id, model_id = str(uuid4()), str(uuid4())
    with TestClient(app):
        summary = import_dataset(app.state.db, settings, small_demo.operational_dir)
        with app.state.db.transaction() as session:
            session.add(Job(id=job_id, status="running", stage="fitting", progress=20, active_key="heavy",
                            created_at=utc_now(), updated_at=utc_now(), dataset_id=summary["dataset_id"], configuration={"task": "training"}))
            session.flush()
            session.add(ModelRun(id=model_id, dataset_id=summary["dataset_id"], job_id=job_id,
                role="final_inference", status="running", training_cutoff=date(2025, 12, 14),
                feature_version="demand-v1", selected_method="pending", seed=42, created_at=utc_now(), details={}))
    with TestClient(create_app(settings)) as client:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        model = client.get(f"/api/v1/models/{model_id}/metrics").json()
        assert job["status"] == model["status"] == "failed"
        assert job["model_id"] == model_id
        assert "retry" in model["error"]
        assert client.get(f"/api/v1/datasets/{summary['dataset_id']}/summary").status_code == 200
