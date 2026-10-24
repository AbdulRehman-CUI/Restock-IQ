import csv
import io
import json
from pathlib import Path


def spreadsheet_safe(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def recommendation_csv(result):
    fields = ["sku", "name", "provenance", "scenario_id", "hypothetical", "current_price_pkr",
        "replacement_cost_pkr", "suggested_price_pkr", "current_replacement_margin", "achievable_margin",
        "effective_lead_days", "first_depletion_date", "first_shortage_date", "shortage_status",
        "reorder_decision_date", "reorder_timing_status", "order_quantity", "order_required", "expedite_required",
        "estimated_replenishment_cash_pkr", "cost_status", "pricing_status", "inventory_status", "quantity_status",
        "cash_status", "warnings", "dataset_id", "model_id", "forecast_run_id", "recommendation_run_id", "origin",
        "horizon_days", "calculation_version"]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fields, lineterminator="\n")
    writer.writeheader()
    for product in result["products"]:
        costs, pricing, inventory = (product[key] for key in ("costs", "pricing", "inventory"))
        row = {key: result[key] for key in ("dataset_id", "model_id", "forecast_run_id", "recommendation_run_id",
                                          "origin", "horizon_days", "calculation_version", "scenario_id", "provenance")}
        row.update(sku=product["sku"], name=product["name"], hypothetical=str(result["scenario_id"] is not None).lower(),
            current_price_pkr=pricing["current_price_pkr"], replacement_cost_pkr=costs["replacement_cost_pkr"],
            suggested_price_pkr=pricing["suggested_price_pkr"], current_replacement_margin=pricing["current_replacement_margin"],
            achievable_margin=pricing["achievable_margin"], effective_lead_days=product["lead_time"]["effective_lead_days"],
            first_depletion_date=inventory["first_depletion_date"], first_shortage_date=inventory["first_shortage_date"],
            shortage_status=inventory["shortage_status"], reorder_decision_date=inventory["timing"]["decision_date"],
            reorder_timing_status=inventory["timing"]["status"], order_quantity=inventory["order_quantity"],
            order_required=str(inventory["order_required"]).lower(), expedite_required=str(inventory["expedite_required"]).lower(),
            estimated_replenishment_cash_pkr=inventory["estimated_replenishment_cash_pkr"], cost_status=costs["status"],
            pricing_status=pricing["status"], inventory_status=inventory["status"], quantity_status=inventory["quantity_status"],
            cash_status=inventory["cash_status"], warnings="|".join(product["reason_codes"]))
        text_fields = ["sku", "name", "provenance", "scenario_id", "warnings", "dataset_id", "model_id", "forecast_run_id"]
        for field in text_fields:
            row[field] = spreadsheet_safe(row[field])
        writer.writerow(row)
    return output.getvalue()


def export_recommendations(db, run_id, output: Path):
    from app.services.recommendations import get_recommendations
    result = get_recommendations(db, run_id)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    sidecar = output.with_suffix(output.suffix + ".metadata.json")
    if sidecar.exists():
        raise FileExistsError(f"Metadata already exists: {sidecar}")
    created = []
    try:
        with output.open("x", encoding="utf-8", newline="") as handle:
            created.append(output)
            handle.write(recommendation_csv(result))
        with sidecar.open("x", encoding="utf-8") as handle:
            created.append(sidecar)
            json.dump({key: value for key, value in result.items() if key != "products"}, handle, indent=2)
            handle.write("\n")
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return {"csv_path": str(output.resolve()), "metadata_path": str(sidecar.resolve()), "row_count": len(result["products"])}
