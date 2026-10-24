from datetime import date, timedelta
from decimal import Decimal as D
from types import SimpleNamespace as NS
from app.services.inventory import classify_supply, estimate_lead_time, future_reorder, inventory_recommendation, project_stock
from test_costs_pricing import product

ORIGIN = date(2026, 10, 15)


def order(po_id="P1", **overrides):
    return NS(**{"po_id": po_id, "sku": "A", "supplier_id": "SUP", "status": "open",
        "ordered_at": ORIGIN - timedelta(days=5), "ordered_units": 100,
        "expected_arrival_date": ORIGIN + timedelta(days=5), "actual_arrival_date": None, **overrides})


def calculate(stock=100, reserved=0, demand=None, orders=(), **settings):
    p = product(**settings)
    snapshot = NS(on_hand_units=stock, reserved_units=reserved)
    lead = estimate_lead_time(p, orders, ORIGIN)
    supply = classify_supply("A", orders, ORIGIN)
    return inventory_recommendation(p, snapshot, [D(10)] * 30 if demand is None else demand, ORIGIN, supply, lead, D("100.00"))


def test_reorder_point_depletion_shortage_and_lost_demand():
    result = calculate()
    assert result["lead_time_demand_units"] == 70 and result["safety_stock_units"] == 20
    assert result["reorder_point_units"] == 90
    assert result["daily"][9]["closing_units"] == 0
    assert result["first_depletion_date"] == ORIGIN + timedelta(days=10)
    assert result["first_shortage_date"] == ORIGIN + timedelta(days=11)
    assert result["daily"][11]["expected_unmet_units"] == 10  # No backlog accumulation.


def test_receipts_before_trading_reservations_once_and_status_filter():
    orders = [order(expected_arrival_date=ORIGIN + timedelta(days=1), ordered_units=10),
              order("cancel", status="cancelled"), order("received", status="received", actual_arrival_date=ORIGIN)]
    result = calculate(stock=20, reserved=10, orders=orders)
    assert result["available_units"] == 10 and result["recorded_inventory_position"] == 20
    assert result["daily"][0]["receipt_units"] == 10
    assert result["daily"][0]["expected_fulfilled_units"] == 10
    assert result["daily"][0]["closing_units"] == 10
    assert result["daily"][1]["closing_units"] == 0


def test_overdue_stays_uncertain_after_scenario_delay():
    overdue = order(expected_arrival_date=ORIGIN, ordered_units=1000)
    supply = classify_supply("A", [overdue], ORIGIN, extra_days=7)
    assert supply["scheduled"] == [] and supply["uncertain"][0]["units"] == 1000
    result = calculate(stock=10, orders=[overdue])
    assert result["recorded_inventory_position"] == 1010
    assert result["planning_inventory_position"] == 10
    assert result["uncertain_overdue_units"] == 1000
    assert all(row["receipt_units"] == 0 for row in result["daily"])
    assert result["order_required"]


def test_distant_receipt_does_not_hide_early_shortage_expedite_existing():
    late = order(expected_arrival_date=ORIGIN + timedelta(days=10), ordered_units=1000)
    result = calculate(stock=10, orders=[late])
    assert result["planning_inventory_position"] == 1010
    assert result["order_quantity"] == 0 and not result["order_required"]
    assert result["expedite_required"] and result["expedite_existing_po_ids"] == ["P1"]
    assert "SHORTAGE_BEFORE_RECEIPT" in result["warnings"]
    assert result["unmet_before_new_arrival_units"] == 50  # Days 2–6; new order arrives start day 7.


def test_moq_pack_horizon_zero_demand_and_missing_forecast():
    result = calculate(stock=150, moq=25, pack_size=12, safety_days=2)
    assert result["raw_order_quantity"] == 10
    assert result["order_quantity"] == 36
    short = calculate(demand=[D(10)] * 5)
    assert short["quantity_status"] == "horizon_insufficient" and short["order_quantity"] is None
    assert len(short["daily"]) == 5 and short["timing"]["status"] == "horizon_insufficient"
    assert short["unmet_before_arrival_status"] == "partial_horizon"
    assert short["unmet_before_arrival_through_date"] == ORIGIN + timedelta(days=5)
    zero = calculate(stock=0, demand=[D(0)] * 30)
    assert zero["order_quantity"] == 0 and not zero["order_required"]
    assert zero["timing"]["status"] == "no_trigger_within_supported_window"
    assert zero["first_shortage_date"] is None
    p = product()
    unknown = inventory_recommendation(p, NS(on_hand_units=100, reserved_units=0), None, ORIGIN,
                                       {"scheduled": [], "uncertain": []}, {"effective_lead_days": 7}, None)
    assert unknown["status"] == "unavailable" and unknown["order_quantity"] is None and unknown["daily"] == []


def test_supplier_lead_median_only_known_received_orders():
    orders = [order(str(i), ordered_at=ORIGIN - timedelta(days=d), actual_arrival_date=ORIGIN, status="received")
              for i, d in enumerate((0, 2, 3, 7, 10, 11))]
    orders += [order("open"), order("cancelled", status="cancelled"),
               order("future", status="received", actual_arrival_date=ORIGIN + timedelta(days=1))]
    lead = estimate_lead_time(product(), orders, ORIGIN, extra_days=7)
    assert lead["base_lead_days"] == 5 and lead["effective_lead_days"] == 12 and lead["observation_count"] == 6
    assert estimate_lead_time(product(), orders[:4], ORIGIN)["method"] == "product_default_fallback"
    same_day = [order(str(i), ordered_at=ORIGIN, actual_arrival_date=ORIGIN, status="received") for i in range(5)]
    assert estimate_lead_time(product(), same_day, ORIGIN)["base_lead_days"] == 1


def test_future_reorder_excludes_receipts_already_added_and_has_complete_window():
    receipt = {"po_id": "P1", "units": 100, "arrival_date": ORIGIN + timedelta(days=1)}
    daily = project_stock(ORIGIN, [D(10)] * 30, 0, [receipt])
    timing = future_reorder(ORIGIN, daily, [receipt], 7, 20, False)
    # Day 1 closes at 90 and incoming stock is no longer pending. Double-counting
    # the receipt here would postpone this threshold until much later.
    assert timing["decision_date"] == ORIGIN + timedelta(days=1)
    no_trigger = future_reorder(ORIGIN, project_stock(ORIGIN, [D(0)] * 30, 100, []), [], 7, 0, False)
    assert no_trigger["last_evaluated_decision_date"] == ORIGIN + timedelta(days=23)


def test_fractional_projection_conserves_units_and_delay_shifts_receipts():
    o = order(ordered_units=2, expected_arrival_date=ORIGIN + timedelta(days=1))
    base = classify_supply("A", [o], ORIGIN)
    delayed = classify_supply("A", [o], ORIGIN, 7)
    assert delayed["scheduled"][0]["arrival_date"] == base["scheduled"][0]["arrival_date"] + timedelta(days=7)
    rows = project_stock(ORIGIN, [D("1.25")] * 10, D("0.5"), base["scheduled"])
    for row in rows:
        assert row["opening_units"] + row["receipt_units"] - row["expected_fulfilled_units"] == row["closing_units"]
        assert row["expected_fulfilled_units"] + row["expected_unmet_units"] == D("1.25")
