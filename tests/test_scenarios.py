from datetime import date, timedelta
from decimal import Decimal
import csv
import io
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.api.main import create_app
from app.core.errors import StateConflict
from app.db.models import (Dataset, ForecastRow, ForecastRun, ModelRun, Product, RecommendationProduct,
                           RecommendationRun, Scenario, TABLES)
from app.db.repositories import get_summary
from app.services.forecasting import get_forecast
from app.services.ingestion import import_dataset, utc_now
from app.services.scenarios import ScenarioAssumptions, create_scenario, get_scenario
from app.services.recommendations import create_recommendations, compare_runs, get_recommendations, get_product
from app.services.exports import export_recommendations, recommendation_csv
from conftest import mutate_csv


def persisted_fixture(db, settings, directory, insufficient=False):
    summary = import_dataset(db, settings, directory)
    origin = date.fromisoformat(summary["date_range"]["end"])
    model_id, forecast_id = str(uuid4()), str(uuid4())
    with db.sessions() as session:
        skus = sorted(session.scalars(select(Product.sku).where(Product.dataset_id == summary["dataset_id"])))
    statuses = [{"sku": sku, "status": "insufficient_history" if insufficient and i == 0 else "forecasted",
                 "method": None if insufficient and i == 0 else "baseline", "fallback_applied": False,
                 "observation_count": 0 if insufficient and i == 0 else 7,
                 "reason_codes": ["insufficient_history"] if insufficient and i == 0 else []} for i, sku in enumerate(skus)]
    rows = [{"run_id": forecast_id, "sku": status["sku"], "date": origin + timedelta(days=i),
             "expected_units": 10.0, "method": "baseline", "fallback_applied": False,
             "observation_count": 7, "reason_codes": []}
            for status in statuses if status["status"] == "forecasted" for i in range(1, 31)]
    # Explicit persisted test forecasts isolate decisions from RF fitting; no fake
    # model or invented evaluation metrics enter application/demo data.
    with db.transaction() as session:
        session.add(ModelRun(id=model_id, dataset_id=summary["dataset_id"], role="final_inference", status="completed",
            training_cutoff=origin, feature_version="test-decision-fixture", selected_method="baseline", seed=42,
            created_at=utc_now(), details={"test_fixture": True}))
        session.flush()
        session.add(ForecastRun(id=forecast_id, dataset_id=summary["dataset_id"], model_id=model_id, origin=origin,
            horizon_days=30, created_at=utc_now(), details={"product_status": statuses, "assumptions": {},
            "feature_version": "test-decision-fixture", "provenance": "uploaded", "forecast_duration_seconds": 0,
            "row_count": len(rows), "forecasted_products": sum(s["status"] == "forecasted" for s in statuses)}))
        session.flush()
        session.execute(insert(ForecastRow), rows)
    return summary["dataset_id"], forecast_id


def test_zero_scenario_repeatability_preserves_data_and_immutability(db, settings, batch_dir):
    dataset_id, forecast_id = persisted_fixture(db, settings, batch_dir)
    before = get_forecast(db, forecast_id)
    summary = get_summary(db, dataset_id)
    with patch("app.ml.artifacts.load_artifact", side_effect=AssertionError("must reuse forecast")), patch("app.ml.train.train_model", side_effect=AssertionError("must not retrain")):
        base = create_recommendations(db, settings, dataset_id, forecast_id)
        again = create_recommendations(db, settings, dataset_id, forecast_id)
        scenario = create_scenario(db, dataset_id, forecast_id, "Zero change", ScenarioAssumptions())
        changed = create_recommendations(db, settings, dataset_id, forecast_id, scenario["id"])
    assert base["products"] == again["products"] == changed["products"]
    assert base["summary"] == changed["summary"]
    assert get_forecast(db, forecast_id) == before and get_summary(db, dataset_id) == summary
    comparison = compare_runs(db, base["recommendation_run_id"], changed["recommendation_run_id"])
    assert all(m["delta"] in ("0", "0.00", "0E-27") or Decimal(m["delta"]) == 0 for key, m in comparison["aggregate"].items() if key != "first_shortage_date" and m["delta"] is not None)
    with pytest.raises(IntegrityError), db.transaction() as session:
        session.execute(update(Scenario).where(Scenario.id == scenario["id"]).values(name="mutated"))
    assert get_scenario(db, scenario["id"])["name"] == "Zero change"


def test_fx_delay_changes_cost_supply_not_demand(db, settings, batch_dir):
    dataset_id, forecast_id = persisted_fixture(db, settings, batch_dir)
    base = create_recommendations(db, settings, dataset_id, forecast_id)
    scenario = create_scenario(db, dataset_id, forecast_id, "FX and delay", ScenarioAssumptions(fx_change_pct=10, extra_lead_days=7))
    changed = create_recommendations(db, settings, dataset_id, forecast_id, scenario["id"])
    for a, b in zip(base["products"], changed["products"]):
        assert [r["expected_demand_units"] for r in a["inventory"]["daily"]] == [r["expected_demand_units"] for r in b["inventory"]["daily"]]
        assert b["lead_time"]["effective_lead_days"] == a["lead_time"]["effective_lead_days"] + 7
        if a["costs"]["currency"] == "PKR":
            assert a["costs"]["replacement_cost_pkr"] == b["costs"]["replacement_cost_pkr"]
        else:
            assert Decimal(b["costs"]["replacement_cost_pkr"]) > Decimal(a["costs"]["replacement_cost_pkr"])
        assert a["inventory"]["supply"]["uncertain"] == b["inventory"]["supply"]["uncertain"]
        for old, new in zip(a["inventory"]["supply"]["scheduled"], b["inventory"]["supply"]["scheduled"]):
            assert date.fromisoformat(new["arrival_date"]) - date.fromisoformat(old["arrival_date"]) == timedelta(days=7)


def test_partial_costs_and_insufficient_forecasts_are_not_zero(db, settings, batch_dir):
    missing_sku = "CBL-002"
    mutate_csv(batch_dir, "supplier_quotes.csv", lambda rows: rows.__setitem__(slice(None), [r for r in rows if r["sku"] != missing_sku]))
    dataset_id, forecast_id = persisted_fixture(db, settings, batch_dir, insufficient=True)
    result = create_recommendations(db, settings, dataset_id, forecast_id)
    # Alphabetically first CBL is intentionally forecast-insufficient, not zero.
    missing = next(p for p in result["products"] if p["sku"] == missing_sku)
    assert missing["costs"]["status"] == missing["pricing"]["status"] == "unavailable"
    assert missing["inventory"]["status"] == "unavailable" and missing["inventory"]["order_quantity"] is None
    assert result["summary"]["estimated_replenishment_cash_pkr"]["partial"]
    assert result["summary"]["estimated_replenishment_cash_pkr"]["excluded_products"] == 1
    row = next(r for r in csv.DictReader(io.StringIO(recommendation_csv(result))) if r["sku"] == missing_sku)
    assert row["replacement_cost_pkr"] == row["order_quantity"] == row["estimated_replenishment_cash_pkr"] == ""


def test_missing_cost_retains_valid_inventory_and_quantities(db, settings, batch_dir):
    mutate_csv(batch_dir, "supplier_quotes.csv", lambda rows: rows.clear())
    dataset_id, forecast_id = persisted_fixture(db, settings, batch_dir)
    result = create_recommendations(db, settings, dataset_id, forecast_id)
    assert all(p["inventory"]["status"] == "available" and len(p["inventory"]["daily"]) == 30 for p in result["products"])
    assert all(p["inventory"]["order_quantity"] is not None for p in result["products"])
    assert all(p["inventory"]["estimated_replenishment_cash_pkr"] is None for p in result["products"])
    assert result["summary"]["estimated_replenishment_cash_pkr"]["value"] is None


def test_invalid_forecast_and_snapshot_model_state_rejected(db, settings, batch_dir):
    dataset_id, forecast_id = persisted_fixture(db, settings, batch_dir)
    with db.transaction() as session:
        row = session.scalars(select(ForecastRow).where(ForecastRow.run_id == forecast_id)).first()
        row.date += timedelta(days=100)
    with pytest.raises(StateConflict, match="consecutive"):
        create_recommendations(db, settings, dataset_id, forecast_id)
    with db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(RecommendationRun)) == 0


def test_nonfinite_persisted_demand_is_rejected(db, settings, batch_dir):
    dataset_id, forecast_id = persisted_fixture(db, settings, batch_dir)
    with db.transaction() as session:
        row = session.scalars(select(ForecastRow).where(ForecastRow.run_id == forecast_id)).first()
        row.expected_units = float("inf")
    with pytest.raises(StateConflict, match="finite"):
        create_recommendations(db, settings, dataset_id, forecast_id)


def test_snapshot_origin_mismatch_and_incomplete_model(db, settings, batch_dir):
    from app.db.models import InventorySnapshot
    dataset_id, forecast_id = persisted_fixture(db, settings, batch_dir)
    with db.transaction() as session:
        session.execute(update(InventorySnapshot).where(InventorySnapshot.dataset_id == dataset_id).values(snapshot_date=date(2000, 1, 1)))
    with pytest.raises(StateConflict, match="snapshots"):
        create_recommendations(db, settings, dataset_id, forecast_id)
    with db.transaction() as session:
        session.execute(update(ModelRun).where(ModelRun.dataset_id == dataset_id).values(status="failed"))
    with pytest.raises(StateConflict, match="completed model"):
        create_recommendations(db, settings, dataset_id, forecast_id)


def test_atomic_recommendation_failure(db, settings, batch_dir):
    from sqlalchemy.orm import Session
    dataset_id, forecast_id = persisted_fixture(db, settings, batch_dir)
    execute = Session.execute
    def fail_products(session, statement, *args, **kwargs):
        if getattr(getattr(statement, "table", None), "name", None) == "recommendation_products":
            raise OSError("injected result write failure")
        return execute(session, statement, *args, **kwargs)
    with patch.object(Session, "execute", fail_products), pytest.raises(OSError):
        create_recommendations(db, settings, dataset_id, forecast_id)
    with db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(RecommendationRun)) == 0
        assert session.scalar(select(func.count()).select_from(RecommendationProduct)) == 0
        assert session.scalar(select(func.count()).select_from(ForecastRow)) == 150
        assert session.scalar(select(func.count()).select_from(Dataset)) == 1


def test_saved_export_safe_text_and_failure_cleanup(db, settings, batch_dir, tmp_path):
    mutate_csv(batch_dir, "products.csv", lambda rows: rows[0].update(name="=HYPERLINK(\"https://example.invalid\")"))
    dataset_id, forecast_id = persisted_fixture(db, settings, batch_dir)
    run = create_recommendations(db, settings, dataset_id, forecast_id)
    assert get_recommendations(db, run["recommendation_run_id"]) == run
    output = tmp_path / "recommendations.csv"
    export_recommendations(db, run["recommendation_run_id"], output)
    assert "'=HYPERLINK" in output.read_text()
    with pytest.raises(FileExistsError):
        export_recommendations(db, run["recommendation_run_id"], output)
    failed = tmp_path / "failed.csv"
    with patch("app.services.exports.json.dump", side_effect=OSError("disk full")), pytest.raises(OSError):
        export_recommendations(db, run["recommendation_run_id"], failed)
    assert not failed.exists() and not failed.with_suffix(".csv.metadata.json").exists()


def test_decision_api_success_errors_and_reference_mismatches(settings, batch_dir):
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        dataset_id, forecast_id = persisted_fixture(app.state.db, settings, batch_dir)
        body = {"dataset_id": dataset_id, "forecast_run_id": forecast_id}
        base = client.post("/api/v1/recommendations", json=body)
        assert base.status_code == 201, base.text
        run_id = base.json()["recommendation_run_id"]
        assert client.get(f"/api/v1/recommendations/{run_id}").json() == base.json()
        product = base.json()["products"][0]
        assert client.get(f"/api/v1/recommendations/{run_id}/products/{product['sku']}").json()["product"] == product
        assert client.get(f"/api/v1/recommendations/{run_id}/products/missing").status_code == 404
        assert client.get(f"/api/v1/recommendations/{run_id}/export").status_code == 200
        scenario = client.post("/api/v1/scenarios", json={**body, "name": "Delay", "fx_change_pct": 10, "extra_lead_days": 7})
        assert scenario.status_code == 201, scenario.text
        sid = scenario.json()["id"]
        assert client.get(f"/api/v1/scenarios/{sid}").json() == scenario.json()
        changed = client.post("/api/v1/recommendations", json={**body, "scenario_id": sid})
        assert changed.status_code == 201, changed.text
        changed_id = changed.json()["recommendation_run_id"]
        assert client.get(f"/api/v1/recommendations/{run_id}/compare/{changed_id}").json()["demand_unchanged"]
        assert client.post("/api/v1/scenarios", json={**body, "name": "invalid", "fx_change_pct": -100}).status_code == 422
        assert client.post("/api/v1/recommendations", json={**body, "pricing": {"price_step_pkr": 0}}).status_code == 422
        assert client.get(f"/api/v1/scenarios/{uuid4()}").status_code == 404
        assert client.get(f"/api/v1/recommendations/{uuid4()}").status_code == 404
        assert client.post("/api/v1/recommendations", json={**body, "forecast_run_id": str(uuid4())}).status_code == 404
        other_dataset, other_forecast = persisted_fixture(app.state.db, settings, batch_dir)
        assert client.post("/api/v1/recommendations", json={**body, "dataset_id": other_dataset}).status_code == 409
        assert client.post("/api/v1/recommendations", json={"dataset_id": other_dataset, "forecast_run_id": other_forecast, "scenario_id": sid}).status_code == 409
        assert client.get(f"/api/v1/recommendations/{changed_id}/compare/{run_id}").status_code == 409
        with patch("app.api.routes.decisions.create_recommendations", side_effect=RuntimeError("SECRET traceback")):
            failure = client.post("/api/v1/recommendations", json=body)
        assert failure.status_code == 500 and failure.json()["error_id"] and "SECRET" not in failure.text
