import csv
from datetime import date
import io
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from sqlalchemy import insert, select
from app.core.errors import NotFound, StateConflict
from app.db.models import ForecastRun, ForecastRow
from app.db.repositories import load_demand_data, model_record
from app.ml.artifacts import load_artifact
from app.ml.features import ASSUMPTIONS, FEATURE_VERSION
from app.ml.predict import predict_recursive
from app.services.ingestion import utc_now
from app.services.exports import spreadsheet_safe


def model_metrics(db, model_id):
    return model_record(db, model_id)


def create_forecast(db, settings, dataset_id, model_id, origin: date, horizon_days: int):
    if not 1 <= horizon_days <= 30:
        raise ValueError("horizon_days must be between 1 and 30")
    started = perf_counter()
    record = model_record(db, model_id)
    if record["dataset_id"] != str(dataset_id):
        raise StateConflict("Model belongs to a different dataset")
    sales, products, provenance = load_demand_data(db, dataset_id)
    if not provenance["start_date"] <= origin <= provenance["end_date"]:
        raise StateConflict("Origin must be within the dataset's observed date range")
    if record["training_cutoff"] > origin:
        raise StateConflict("Model training cutoff is after requested origin; use the appropriate evaluation model")
    candidate = load_artifact(settings, record)
    rows, statuses = predict_recursive(sales, products, origin, horizon_days, candidate, record["selected_method"])
    run_id = str(uuid4())
    details = {"product_status": statuses, "assumptions": ASSUMPTIONS,
               "feature_version": FEATURE_VERSION, "provenance": provenance["provenance"],
               "forecast_duration_seconds": perf_counter() - started, "row_count": len(rows),
               "forecasted_products": sum(p["status"] == "forecasted" for p in statuses)}
    with db.transaction() as session:
        session.add(ForecastRun(id=run_id, dataset_id=str(dataset_id), model_id=str(model_id), origin=origin,
            horizon_days=horizon_days, created_at=utc_now(), details=details))
        session.flush()
        if rows:
            session.execute(insert(ForecastRow), [{"run_id": run_id, **row} for row in rows])
    return get_forecast(db, run_id)


def get_forecast(db, run_id):
    with db.sessions() as session:
        run = session.get(ForecastRun, str(run_id))
        if run is None:
            raise NotFound("Forecast run not found")
        result = {"forecast_run_id": run.id, "dataset_id": run.dataset_id, "model_id": run.model_id,
                  "origin": run.origin.isoformat(), "horizon_days": run.horizon_days,
                  "created_at": run.created_at, **run.details}
        rows = session.scalars(select(ForecastRow).where(ForecastRow.run_id == run.id).order_by(ForecastRow.sku, ForecastRow.date))
        result["rows"] = [{"sku": r.sku, "date": r.date.isoformat(), "expected_units": r.expected_units,
                           "method": r.method, "fallback_applied": r.fallback_applied,
                           "observation_count": r.observation_count, "reason_codes": r.reason_codes,
                           "origin": result["origin"], "horizon_days": run.horizon_days,
                           "model_id": run.model_id, "dataset_id": run.dataset_id} for r in rows]
        return result


def csv_text(result):
    fields = ["dataset_id", "model_id", "forecast_run_id", "origin", "horizon_days", "sku", "date",
              "expected_units", "method", "fallback_applied", "observation_count", "reason_codes", "provenance"]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fields, lineterminator="\n")
    writer.writeheader()
    for row in result["rows"]:
        values = {**row, "forecast_run_id": result["forecast_run_id"], "provenance": result["provenance"]}
        values["reason_codes"] = "|".join(values["reason_codes"])
        values["fallback_applied"] = str(values["fallback_applied"]).lower()
        # Preserve numeric fractional units; escape formula-like text only.
        for field, value in values.items():
            values[field] = spreadsheet_safe(value)
        writer.writerow(values)
    return output.getvalue()


def export_forecast(db, run_id, output: Path):
    result = get_forecast(db, run_id)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    sidecar = output.with_suffix(output.suffix + ".metadata.json")
    if sidecar.exists():
        raise FileExistsError(f"Export metadata already exists: {sidecar}")
    # Exclusive creation never replaces old exports. On failure remove only files
    # that this invocation successfully created, leaving no misleading partial pair.
    created_csv = created_metadata = False
    try:
        with output.open("x", encoding="utf-8", newline="") as handle:
            created_csv = True
            handle.write(csv_text(result))
        with sidecar.open("x", encoding="utf-8") as handle:
            created_metadata = True
            json.dump({key: value for key, value in result.items() if key != "rows"}, handle, indent=2)
            handle.write("\n")
    except Exception:
        if created_metadata:
            sidecar.unlink(missing_ok=True)
        if created_csv:
            output.unlink(missing_ok=True)
        raise
    return {"csv_path": str(output.resolve()), "metadata_path": str(sidecar.resolve()), "row_count": result["row_count"]}
