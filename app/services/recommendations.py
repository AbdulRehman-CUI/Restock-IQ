"""Compose pure decision formulas, then persist the validated result atomically."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import insert, select

from app.api.schemas.decisions import ProductResult
from app.core.errors import NotFound, StateConflict
from app.db.models import RecommendationRun, RecommendationProduct
from app.db.repositories import get_summary, load_business_inputs, model_record
from app.services.costs import replacement_cost, money
from app.services.forecasting import get_forecast
from app.services.ingestion import utc_now
from app.services.inventory import classify_supply, estimate_lead_time, inventory_recommendation
from app.services.pricing import PricingSettings, price_recommendation
from app.services.scenarios import ScenarioAssumptions, get_scenario

CALCULATION_VERSION = "decisions-v1"
ASSUMPTIONS = [
    "Demand is a fractional point estimate, unchanged by scenarios or suggested prices; future store days open and promotions inactive.",
    "Unmet demand is lost, never carried forward as a backlog. Receipts arrive before that day's trading.",
    "End-of-day orders arrive at the start of origin plus effective lead days; urgency excludes that arrival day's demand.",
    "Safety stock is ceil(mean demand over the entire supplied horizon times safety_days), a heuristic rather than a service-level guarantee.",
    "Overdue supply is uncertain and excluded from reliable planning, including under delay scenarios.",
    "Order quantity is today's coverage-based candidate; act now only when order_required, otherwise consult timing and urgency.",
    "Costs are held constant over the planning horizon. Scenario costs are assumptions; suggested prices do not establish willingness to pay.",
    "Cash is estimated landed replenishment cost, not a supplier invoice. Historical gross difference is not net profit.",
    "Synthetic results do not demonstrate real business savings; no calibrated forecast uncertainty is supplied.",
]


def wire(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [wire(item) for item in value]
    return value


def load_analysis_inputs(db, dataset_id, forecast_run_id):
    dataset = get_summary(db, dataset_id)
    forecast = get_forecast(db, forecast_run_id)
    model = model_record(db, forecast["model_id"])
    if forecast["dataset_id"] != str(dataset_id) or model["dataset_id"] != str(dataset_id):
        raise StateConflict("Dataset, forecast and model references must belong together")
    origin, horizon = date.fromisoformat(forecast["origin"]), forecast["horizon_days"]
    if model["status"] != "completed" or model["training_cutoff"] > origin:
        raise StateConflict("Analysis requires a completed model trained no later than the forecast origin")
    inputs = load_business_inputs(db, dataset_id)
    products = {p.sku: p for p in inputs["products.csv"]}
    snapshots = {p.sku: p for p in inputs["inventory.csv"]}
    if set(snapshots) != set(products) or any(s.snapshot_date != origin for s in snapshots.values()):
        raise StateConflict("Inventory snapshots must match the forecast origin; historical stock cannot be reconstructed")
    statuses = {p["sku"]: p for p in forecast["product_status"]}
    if set(statuses) != set(products) or len(statuses) != len(forecast["product_status"]):
        raise StateConflict("Forecast must explicitly report the status of every dataset product")
    grouped = {sku: [] for sku in products}
    if forecast["row_count"] != len(forecast["rows"]) or not 1 <= horizon <= 30:
        raise StateConflict("Persisted forecast is not complete")
    for row in forecast["rows"]:
        if row["sku"] not in products:
            raise StateConflict("Forecast references an unknown product")
        value = Decimal(str(row["expected_units"]))
        if not value.is_finite() or value < 0:
            raise StateConflict("Forecast demand must be finite and nonnegative")
        grouped[row["sku"]].append((date.fromisoformat(row["date"]), value))
    demands = {}
    expected_dates = [origin + timedelta(days=i) for i in range(1, horizon + 1)]
    for sku, values in grouped.items():
        if statuses[sku]["status"] == "forecasted":
            if [d for d, _ in sorted(values)] != expected_dates:
                raise StateConflict(f"Forecast for {sku} must have consecutive dates beginning the day after origin")
            demands[sku] = [v for _, v in sorted(values)]
        elif statuses[sku]["status"] == "insufficient_history" and not values:
            demands[sku] = None
        else:
            raise StateConflict(f"Forecast rows contradict the availability status of {sku}")
    # ForecastRun is created with all its rows in one transaction in Phase 2;
    # a persisted, reconciled row/status set is its completed state.
    return dataset, forecast, inputs, demands


def explanation(product, cost, pricing, inventory, lead, horizon):
    lines = [f"Lead time: {lead['effective_lead_days']} calendar days via {lead['method']} from {lead['observation_count']} eligible supplier receipts; scenario adds {lead['scenario_extra_days']} days."]
    if cost["replacement_cost_pkr"] is None:
        lines.append("Replacement cost and pricing are unavailable; historical purchase cost is not substituted.")
    else:
        lines.append(f"Quote {cost['quote_id']} yields landed replacement cost PKR {cost['replacement_cost_pkr']}; acquisition plus explicit per-unit freight and other charges.")
        lines.append(f"Price PKR {pricing['suggested_price_pkr']} has achievable replacement margin {pricing['achievable_margin']}; target fraction {pricing['target_margin']}.")
    lines.append(f"Available stock is on-hand {product['on_hand_units']} minus reserved {product['reserved_units']}, deducted once.")
    if inventory["status"] == "unavailable":
        lines.append("No usable forecast: stock decisions are unavailable, rather than assuming zero demand.")
    else:
        lines.append(f"Safety stock {inventory['safety_stock_units']} units uses mean daily demand across all {horizon} forecast days. This is a heuristic buffer.")
        lines.append(f"Recorded position {inventory['recorded_inventory_position']} units; reliable planning position {inventory['planning_inventory_position']} excludes {inventory['uncertain_overdue_units']} uncertain units.")
        lines.append(f"Quantity status {inventory['quantity_status']}: coverage {inventory['coverage_days']} days, raw quantity {inventory['raw_order_quantity']}, MOQ {product['moq']}, pack {product['pack_size']}, coverage order quantity {inventory['order_quantity']}.")
        if inventory["expedite_required"]:
            lines.append("Demand is expected to go unmet before a new order's arrival. Expedite existing supply where possible; a normal new order cannot prevent that earlier shortage.")
        if inventory["order_quantity"] == 0 and inventory["expedite_existing_po_ids"]:
            lines.append("Later existing receipts cover the coverage need: expedite those orders instead of creating a duplicate order.")
        lines.append(f"Timing {inventory['timing']['status']}; last evaluated decision {inventory['timing']['last_evaluated_decision_date']}. A null date is limited to the supported window.")
    return lines


METRICS = {"replacement_cost_pkr": ("costs", "replacement_cost_pkr"),
           "suggested_price_pkr": ("pricing", "suggested_price_pkr"),
           "current_replacement_margin": ("pricing", "current_replacement_margin"),
           "expected_unmet_units": ("inventory", "expected_unmet_units"),
           "order_quantity": ("inventory", "order_quantity"),
           "estimated_replenishment_cash_pkr": ("inventory", "estimated_replenishment_cash_pkr")}


def summarize(products):
    summary = {"product_count": len(products), "cost_available_products": sum(p["costs"]["status"] != "unavailable" for p in products),
        "inventory_available_products": sum(p["inventory"]["status"] == "available" for p in products),
        "order_required_products": sum(p["inventory"]["order_required"] for p in products),
        "expedite_required_products": sum(p["inventory"]["expedite_required"] for p in products),
        "shortage_products": sum(p["inventory"]["first_shortage_date"] is not None for p in products),
        "horizon_insufficient_products": sum(p["inventory"]["quantity_status"] == "horizon_insufficient" for p in products),
        "overdue_supply_products": sum(Decimal(p["inventory"]["uncertain_overdue_units"]) > 0 for p in products)}
    for metric, (component, field) in METRICS.items():
        values = [Decimal(str(p[component][field])) for p in products if p[component][field] is not None]
        total = sum(values, Decimal(0)) if values else None
        if metric == "current_replacement_margin" and values:
            total /= len(values)
        summary[metric] = {"value": total, "aggregation": "unweighted_mean" if metric == "current_replacement_margin" else "sum",
            "included_products": len(values), "excluded_products": len(products) - len(values), "partial": len(values) != len(products)}
    summary["cash_scope"] = "All calculable coverage order quantities; check order_required before acting. Partial totals exclude unavailable cash."
    return wire(summary)


def create_recommendations(db, settings, dataset_id, forecast_run_id, scenario_id=None, pricing_settings=None):
    dataset, forecast, inputs, demands = load_analysis_inputs(db, dataset_id, forecast_run_id)
    scenario = get_scenario(db, scenario_id) if scenario_id else None
    if scenario and (scenario["dataset_id"] != str(dataset_id) or scenario["forecast_run_id"] != str(forecast_run_id)):
        raise StateConflict("Scenario must reference this exact dataset and forecast")
    assumptions = ScenarioAssumptions.model_validate(scenario["assumptions"] if scenario else {})
    pricing_settings = PricingSettings.model_validate(pricing_settings or PricingSettings())
    origin = date.fromisoformat(forecast["origin"])
    snapshots = {s.sku: s for s in inputs["inventory.csv"]}
    statuses = {s["sku"]: s for s in forecast["product_status"]}
    products = []
    for product in sorted(inputs["products.csv"], key=lambda p: p.sku):
        cost = replacement_cost(product, inputs["supplier_quotes.csv"], inputs["fx_rates.csv"], origin, settings, assumptions)
        pricing = price_recommendation(product, cost["replacement_cost_pkr"],
            assumptions.target_margin_override if assumptions.target_margin_override is not None else product.target_margin, pricing_settings)
        lead = estimate_lead_time(product, inputs["purchase_orders.csv"], origin, assumptions.extra_lead_days)
        supply = classify_supply(product.sku, inputs["purchase_orders.csv"], origin, assumptions.extra_lead_days)
        inventory = inventory_recommendation(product, snapshots[product.sku], demands[product.sku], origin,
                                             supply, lead, cost["replacement_cost_pkr"])
        effective_inputs = {**product.model_dump(), **snapshots[product.sku].model_dump()}
        result = {"sku": product.sku, "name": product.name, "inputs": effective_inputs,
            "forecast": {**statuses[product.sku], "forecast_run_id": str(forecast_run_id), "model_id": forecast["model_id"],
                         "dataset_id": str(dataset_id), "origin": origin, "horizon_days": forecast["horizon_days"]},
            "lead_time": lead, "costs": cost, "pricing": pricing, "inventory": inventory,
            "reason_codes": sorted(set(cost["warnings"] + pricing["warnings"] + inventory["warnings"] + statuses[product.sku]["reason_codes"])),
            "explanations": explanation(effective_inputs, cost, pricing, inventory, lead, forecast["horizon_days"])}
        products.append(ProductResult.model_validate(wire(result)).model_dump(mode="json"))
    run_id = str(uuid4())
    details = {"effective_settings": {"pricing": pricing_settings.model_dump(mode="json"),
        "fx_stale_days": settings.fx_stale_days, "quote_stale_days": settings.quote_stale_days,
        "money_rounding": "Round landed cost once to PKR cents, ROUND_HALF_UP; reuse for prices and cash.",
        "safety_average_window_days": forecast["horizon_days"]}, "scenario": scenario,
        "provenance": dataset["manifest"]["provenance"], "assumptions": ASSUMPTIONS, "summary": summarize(products)}
    with db.transaction() as session:
        session.add(RecommendationRun(id=run_id, dataset_id=str(dataset_id), forecast_run_id=str(forecast_run_id),
            model_id=forecast["model_id"], scenario_id=str(scenario_id) if scenario_id else None,
            origin=origin, horizon_days=forecast["horizon_days"], calculation_version=CALCULATION_VERSION,
            created_at=utc_now(), details=wire(details)))
        session.flush()
        session.execute(insert(RecommendationProduct), [{"run_id": run_id, "sku": p["sku"], "result": p} for p in products])
    return get_recommendations(db, run_id)


def get_recommendations(db, run_id):
    with db.sessions() as session:
        run = session.get(RecommendationRun, str(run_id))
        if run is None:
            raise NotFound("Recommendation run not found")
        result = {"recommendation_run_id": run.id, **{key: getattr(run, key) for key in (
            "dataset_id", "forecast_run_id", "model_id", "scenario_id", "origin", "horizon_days", "calculation_version", "created_at")}, **run.details}
        result["products"] = [p.result for p in session.scalars(select(RecommendationProduct).where(
            RecommendationProduct.run_id == run.id).order_by(RecommendationProduct.sku))]
        return wire(result)


def get_product(db, run_id, sku):
    run = get_recommendations(db, run_id)
    product = next((p for p in run["products"] if p["sku"] == sku), None)
    if product is None:
        raise NotFound("Product not found in recommendation run")
    return {"recommendation_run_id": run["recommendation_run_id"], "product": product}


def compare_runs(db, base_id, scenario_id):
    base, scenario = get_recommendations(db, base_id), get_recommendations(db, scenario_id)
    if base["scenario_id"] is not None or scenario["scenario_id"] is None:
        raise StateConflict("Compare a base run with a scenario run, in that order")
    for field in ("dataset_id", "forecast_run_id", "origin", "horizon_days", "calculation_version", "effective_settings"):
        if base[field] != scenario[field]:
            raise StateConflict(f"Comparison requires identical {field}")
    right = {p["sku"]: p for p in scenario["products"]}
    comparisons = []
    for left in base["products"]:
        other = right[left["sku"]]
        values = {"sku": left["sku"], "name": left["name"]}
        for metric, (component, field) in METRICS.items():
            a, b = left[component][field], other[component][field]
            values[metric] = {"base": a, "scenario": b, "delta": Decimal(str(b)) - Decimal(str(a)) if a is not None and b is not None else None}
        a, b = left["inventory"]["first_shortage_date"], other["inventory"]["first_shortage_date"]
        values["first_shortage_date"] = {"base": a, "scenario": b,
            "shift_days": (date.fromisoformat(b) - date.fromisoformat(a)).days if a and b else None,
            "base_status": left["inventory"]["shortage_status"], "scenario_status": other["inventory"]["shortage_status"]}
        comparisons.append(values)
    aggregate = {}
    for metric in METRICS:
        pairs = [p[metric] for p in comparisons if p[metric]["delta"] is not None]
        a = sum((Decimal(str(p["base"])) for p in pairs), Decimal(0)) if pairs else None
        b = sum((Decimal(str(p["scenario"])) for p in pairs), Decimal(0)) if pairs else None
        if metric == "current_replacement_margin" and pairs:
            a, b = a / len(pairs), b / len(pairs)
        aggregate[metric] = {"base": a, "scenario": b, "delta": b - a if pairs else None,
            "aggregation": "unweighted_mean_on_common_products" if metric == "current_replacement_margin" else "sum_on_common_products",
            "included_products": len(pairs), "excluded_products": len(comparisons) - len(pairs), "partial": len(pairs) < len(comparisons)}
    aggregate["first_shortage_date"] = {"base_earliest": min((p["first_shortage_date"]["base"] for p in comparisons if p["first_shortage_date"]["base"]), default=None),
        "scenario_earliest": min((p["first_shortage_date"]["scenario"] for p in comparisons if p["first_shortage_date"]["scenario"]), default=None),
        "base_shortage_products": base["summary"]["shortage_products"], "scenario_shortage_products": scenario["summary"]["shortage_products"],
        "included_products": base["summary"]["inventory_available_products"],
        "excluded_products": len(comparisons) - base["summary"]["inventory_available_products"]}
    shortage_comparison = aggregate["first_shortage_date"]
    a, b = shortage_comparison["base_earliest"], shortage_comparison["scenario_earliest"]
    shortage_comparison["earliest_shift_days"] = (date.fromisoformat(b) - date.fromisoformat(a)).days if a and b else None
    shortage_comparison["shortage_product_count_delta"] = scenario["summary"]["shortage_products"] - base["summary"]["shortage_products"]
    return wire({"base_run_id": str(base_id), "scenario_run_id": str(scenario_id),
                 "forecast_run_id": base["forecast_run_id"], "demand_unchanged": True, "aggregate": aggregate, "products": comparisons})
