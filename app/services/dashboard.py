"""Read projections over saved operational results; historical runs stay untouched."""
from decimal import Decimal
from sqlalchemy import select
from app.core.errors import NotFound, StateConflict
from app.db.models import (Dataset, ModelRun, ForecastRun, RecommendationRun, Scenario,
                           DailySale, Product, RecommendationAction, RecommendationProduct)
from app.services.ingestion import utc_now


CATALOG = {
    "datasets": (Dataset, ("id", "provenance", "start_date", "end_date")),
    "models": (ModelRun, ("id", "dataset_id", "status", "role", "training_cutoff", "selected_method", "created_at")),
    "forecasts": (ForecastRun, ("id", "dataset_id", "model_id", "origin", "horizon_days", "created_at")),
    "recommendations": (RecommendationRun, ("id", "dataset_id", "model_id", "forecast_run_id", "scenario_id", "origin", "created_at")),
    "scenarios": (Scenario, ("id", "dataset_id", "forecast_run_id", "name", "created_at")),
}


def discover(db, resource, **filters):
    model, fields = CATALOG[resource]
    query = select(*(getattr(model, f) for f in fields))
    for field, value in filters.items():
        if value is not None:
            query = query.where(getattr(model, field) == str(value))
    if resource == "models":
        query = query.where(ModelRun.role == "final_inference")
    order = model.start_date if resource == "datasets" else model.created_at
    with db.sessions() as session:
        return [dict(row) for row in session.execute(query.order_by(order.desc(), model.id.desc())).mappings()]


def sales_history(db, dataset_id, sku, through):
    with db.sessions() as session:
        if session.get(Product, {"dataset_id": str(dataset_id), "sku": sku}) is None:
            raise NotFound("Product not found in this dataset")
        rows = session.execute(select(DailySale.date, DailySale.units_sold, DailySale.fully_in_stock,
            DailySale.store_open, DailySale.promo_active).where(DailySale.dataset_id == str(dataset_id),
            DailySale.sku == sku, DailySale.date <= through).order_by(DailySale.date)).mappings()
        return {"dataset_id": str(dataset_id), "sku": sku, "through": through, "rows": [dict(r) for r in rows]}


def overview(products):
    candidates = [p for p in products if p["inventory"]["order_required"]]
    values = [Decimal(str(p["inventory"]["estimated_replenishment_cash_pkr"])) for p in candidates
              if p["inventory"]["estimated_replenishment_cash_pkr"] is not None]
    below = [p for p in products if p["pricing"]["current_replacement_margin"] is not None and
             Decimal(str(p["pricing"]["current_replacement_margin"])) < Decimal(str(p["pricing"]["target_margin"]))]
    return {"order_now_products": len(candidates), "expedite_products": sum(p["inventory"]["expedite_required"] for p in products),
        "below_target_margin_products": len(below),
        "shortage_products": sum(p["inventory"]["first_shortage_date"] is not None for p in products),
        "overdue_supply_products": sum(p["inventory"]["uncertain_overdue_units"] > 0 for p in products),
        "order_now_cash": {"value": str(sum(values)) if values else None,
            "included_products": len(values), "excluded_products": len(candidates) - len(values),
            "partial": len(values) < len(candidates), "scope": "Landed cash for coverage candidates with order_required=true only; not an invoice."}}


def action_history(db, run_id):
    with db.sessions() as session:
        if session.get(RecommendationRun, str(run_id)) is None:
            raise NotFound("Recommendation run not found")
        rows = session.scalars(select(RecommendationAction).where(RecommendationAction.run_id == str(run_id))
                               .order_by(RecommendationAction.timestamp.desc(), RecommendationAction.request_id.desc()))
        return [action_record(r) for r in rows]


def action_record(row):
    return {c.name: getattr(row, c.name) for c in RecommendationAction.__table__.columns}


def record_action(db, run_id, request_id, sku, action, note):
    payload = {"run_id": str(run_id), "sku": sku, "action": action, "note": note}
    with db.transaction() as session:
        existing = session.get(RecommendationAction, str(request_id))
        if existing:
            if any(getattr(existing, k) != v for k, v in payload.items()):
                raise StateConflict("Request ID already used for another action")
            return action_record(existing)
        if session.get(RecommendationProduct, (str(run_id), sku)) is None:
            raise NotFound("Product not found in this recommendation run")
        row = RecommendationAction(request_id=str(request_id), timestamp=utc_now(), **payload)
        session.add(row)
        session.flush()
        return action_record(row)
