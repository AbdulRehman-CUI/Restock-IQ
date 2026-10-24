"""Deterministic lost-demand stock projections; no model inference or purchasing."""
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING
from statistics import median
from math import ceil
from app.services.costs import money


def ceiling(value):
    return int(Decimal(value).to_integral_value(rounding=ROUND_CEILING))


def estimate_lead_time(product, orders, origin, extra_days=0):
    durations = [(p.actual_arrival_date - p.ordered_at).days for p in orders
                 if p.supplier_id == product.supplier_id and p.status == "received"
                 and p.actual_arrival_date is not None and p.ordered_at <= p.actual_arrival_date <= origin]
    observed = len(durations) >= 5
    base = max(1, ceil(median(durations))) if observed else product.default_lead_days
    return {"base_lead_days": base, "effective_lead_days": base + extra_days,
            "scenario_extra_days": extra_days, "observation_count": len(durations),
            "method": "ceil_supplier_median" if observed else "product_default_fallback"}


def classify_supply(sku, orders, origin, extra_days=0):
    scheduled, uncertain = [], []
    for order in sorted(orders, key=lambda p: p.po_id):
        if order.sku != sku or order.status != "open" or order.ordered_at > origin:
            continue
        entry = {"po_id": order.po_id, "units": order.ordered_units,
                 "recorded_expected_arrival_date": order.expected_arrival_date}
        if order.expected_arrival_date <= origin or order.actual_arrival_date is not None:
            uncertain.append({**entry, "reason": "OVERDUE_SUPPLY_UNCERTAIN"})
        else:
            scheduled.append({**entry, "arrival_date": order.expected_arrival_date + timedelta(days=extra_days)})
    return {"scheduled": sorted(scheduled, key=lambda p: (p["arrival_date"], p["po_id"])), "uncertain": uncertain}


def project_stock(origin, demand, available, scheduled):
    stock, daily = Decimal(available), []
    for offset, expected in enumerate(demand, 1):
        day = origin + timedelta(days=offset)
        opening = stock
        receipts = sum(p["units"] for p in scheduled if p["arrival_date"] == day)
        trading = opening + receipts
        fulfilled, unmet = min(trading, expected), max(Decimal(0), expected - trading)
        stock = max(Decimal(0), trading - expected)
        daily.append({"date": day, "opening_units": opening, "receipt_units": receipts,
                      "expected_demand_units": expected, "expected_fulfilled_units": fulfilled,
                      "expected_unmet_units": unmet, "closing_units": stock})
    return daily


def future_reorder(origin, daily, scheduled, lead_days, safety, order_now):
    horizon = len(daily)
    if lead_days > horizon:
        return {"status": "horizon_insufficient", "decision_date": None, "last_evaluated_decision_date": None}
    if order_now:
        return {"status": "order_now", "decision_date": origin, "last_evaluated_decision_date": origin}
    last = origin
    for index in range(horizon - lead_days):
        row = daily[index]
        last = row["date"]
        # Only orders still pending AFTER this end-of-day candidate are added.
        position = row["closing_units"] + sum(p["units"] for p in scheduled if p["arrival_date"] > last)
        threshold = ceiling(sum(r["expected_demand_units"] for r in daily[index + 1:index + 1 + lead_days]) + safety)
        if threshold > 0 and position <= threshold:
            return {"status": "estimated_future_decision", "decision_date": last, "last_evaluated_decision_date": last}
    return {"status": "no_trigger_within_supported_window", "decision_date": None, "last_evaluated_decision_date": last}


def inventory_recommendation(product, snapshot, demand, origin, supply, lead, cost):
    available = snapshot.on_hand_units - snapshot.reserved_units
    scheduled, uncertain = supply["scheduled"], supply["uncertain"]
    reliable_units = sum(p["units"] for p in scheduled)
    uncertain_units = sum(p["units"] for p in uncertain)
    result = {"status": "unavailable" if demand is None else "available", "available_units": available,
        "total_open_ordered_units": reliable_units + uncertain_units, "scheduled_incoming_units": reliable_units,
        "uncertain_overdue_units": uncertain_units, "recorded_inventory_position": available + reliable_units + uncertain_units,
        "planning_inventory_position": available + reliable_units, "supply": supply, "daily": [],
        "first_depletion_date": None, "first_shortage_date": None, "shortage_status": "insufficient_forecast",
        "expected_unmet_units": None, "lead_time_demand_units": None, "safety_stock_units": None,
        "reorder_point_units": None, "coverage_days": lead["effective_lead_days"] + product.review_days,
        "coverage_demand_units": None, "coverage_receipt_units": None, "raw_order_quantity": None,
        "order_quantity": None, "quantity_status": "insufficient_forecast", "order_required": False,
        "supply_action_required": False, "expedite_required": False, "unmet_before_new_arrival_units": None,
        "unmet_before_arrival_status": "insufficient_forecast", "unmet_before_arrival_through_date": None,
        "new_order_arrival_date": origin + timedelta(days=lead["effective_lead_days"]),
        "expedite_existing_po_ids": [], "estimated_replenishment_cash_pkr": None,
        "cash_status": "insufficient_forecast", "timing": {"status": "insufficient_forecast", "decision_date": None,
        "last_evaluated_decision_date": None}, "warnings": ["OVERDUE_SUPPLY_UNCERTAIN"] if uncertain else []}
    if demand is None:
        result["warnings"].append("INSUFFICIENT_FORECAST")
        return result
    if not demand or any(not x.is_finite() or x < 0 for x in demand):
        raise ValueError("Demand must be a nonempty finite, nonnegative Decimal series")
    daily = project_stock(origin, demand, available, scheduled)
    horizon, lead_days = len(demand), lead["effective_lead_days"]
    safety = ceiling(sum(demand) / horizon * product.safety_days)
    depletion = next((r["date"] for r in daily if r["closing_units"] == 0), None)
    shortage = next((r["date"] for r in daily if r["expected_unmet_units"] > 0), None)
    lead_demand = sum(demand[:lead_days]) if lead_days <= horizon else None
    point = ceiling(lead_demand + safety) if lead_demand is not None else None
    next_receipt = min((p["arrival_date"] for p in scheduled), default=None)
    before_receipt = shortage is not None and (next_receipt is None or shortage < next_receipt)
    threshold = point is not None and result["planning_inventory_position"] <= point
    if threshold:
        result["warnings"].append("REORDER_THRESHOLD_REACHED")
    if before_receipt:
        result["warnings"].append("SHORTAGE_BEFORE_RECEIPT")
    coverage = result["coverage_days"]
    if coverage <= horizon:
        receipt_units = sum(p["units"] for p in scheduled if p["arrival_date"] <= origin + timedelta(days=coverage))
        coverage_demand = sum(demand[:coverage])
        raw = max(0, ceiling(coverage_demand + safety - available - receipt_units))
        quantity = ((max(raw, product.moq) + product.pack_size - 1) // product.pack_size) * product.pack_size if raw else 0
        result.update(quantity_status="available", coverage_demand_units=coverage_demand,
                      coverage_receipt_units=receipt_units, raw_order_quantity=raw, order_quantity=quantity)
        result["cash_status"] = "available" if cost is not None else "cost_unavailable"
        if cost is not None:
            result["estimated_replenishment_cash_pkr"] = money(quantity * cost)
    else:
        result["quantity_status"], result["cash_status"] = "horizon_insufficient", "horizon_insufficient"
        result["warnings"].append("HORIZON_INSUFFICIENT")
    if lead_days > horizon and "HORIZON_INSUFFICIENT" not in result["warnings"]:
        result["warnings"].append("HORIZON_INSUFFICIENT")
    unmet_before = sum(r["expected_unmet_units"] for r in daily if r["date"] < result["new_order_arrival_date"])
    expedite = unmet_before > 0
    if expedite:
        result["warnings"].append("EXPEDITE_REQUIRED")
    quantity = result["order_quantity"]
    action = (threshold and point > 0) or before_receipt
    order_required = bool(action and quantity is not None and quantity > 0)
    result.update(daily=daily, first_depletion_date=depletion, first_shortage_date=shortage,
        shortage_status="shortage_within_horizon" if shortage else "no_shortage_within_horizon",
        expected_unmet_units=sum(r["expected_unmet_units"] for r in daily), lead_time_demand_units=lead_demand,
        safety_stock_units=safety, reorder_point_units=point, order_required=order_required,
        supply_action_required=bool(action or expedite), expedite_required=expedite,
        unmet_before_new_arrival_units=unmet_before,
        unmet_before_arrival_status="available" if lead_days - 1 <= horizon else "partial_horizon",
        unmet_before_arrival_through_date=min(origin + timedelta(days=horizon), result["new_order_arrival_date"] - timedelta(days=1)),
        expedite_existing_po_ids=[p["po_id"] for p in scheduled if shortage and p["arrival_date"] > shortage] if expedite or before_receipt else [],
        timing=future_reorder(origin, daily, scheduled, lead_days, safety,
                              order_required or (quantity is None and bool(action))))
    return result
