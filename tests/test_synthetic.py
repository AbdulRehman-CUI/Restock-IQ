from datetime import date
import hashlib

from app.data.contracts import CONTRACTS
from app.data.synthetic import GeneratorConfig, generate_demo
from app.services.validation import read_batch
from conftest import read_csv


def test_deterministic_records_and_hashes(demo, settings):
    repeat = generate_demo(settings, GeneratorConfig())
    assert repeat.dataset_id != demo.dataset_id
    assert repeat.manifest["file_hashes"] == demo.manifest["file_hashes"]
    for filename in CONTRACTS:
        assert (repeat.operational_dir / filename).read_bytes() == (demo.operational_dir / filename).read_bytes()
        assert hashlib.sha256((demo.operational_dir / filename).read_bytes()).hexdigest() == demo.manifest["file_hashes"][filename]
    for filename in ("simulation.csv", "order_truth.csv"):
        assert (repeat.evaluation_dir / filename).read_bytes() == (demo.evaluation_dir / filename).read_bytes()
    other = generate_demo(settings, GeneratorConfig(seed=43, days=60, sku_count=5))
    assert other.manifest["file_hashes"]["daily_sales.csv"] != demo.manifest["file_hashes"]["daily_sales.csv"]


def test_default_coverage_and_provenance(demo):
    batch = read_batch(demo.operational_dir)
    assert batch.start_date == date(2025, 10, 16)
    assert batch.end_date == date(2026, 10, 15)
    assert len(batch.tables["products.csv"]) == 20
    assert len(batch.tables["daily_sales.csv"]) == 7300
    assert len({(s.sku, s.date) for s in batch.tables["daily_sales.csv"]}) == 7300
    final_prices = {s.sku: s.selling_price_pkr for s in batch.tables["daily_sales.csv"] if s.date == batch.end_date}
    assert all(p.current_price_pkr == final_prices[p.sku] for p in batch.tables["products.csv"])
    assert {q.currency for q in batch.tables["supplier_quotes.csv"]} == {"USD", "PKR"}
    assert all(r.source == "synthetic" and r.pkr_per_unit > 0 for r in batch.tables["fx_rates.csv"])
    assert demo.manifest["provenance"] == "synthetic"
    assert demo.manifest["timezone"] == "Asia/Karachi"
    assert demo.manifest["validation_summary"]["readiness"]["current_cost_inputs_available"]


def test_inventory_receipts_sales_and_origin(demo):
    batch = read_batch(demo.operational_dir)
    orders = {p.po_id: p for p in batch.tables["purchase_orders.csv"]}
    sales = {(s.sku, s.date.isoformat()): s for s in batch.tables["daily_sales.csv"]}
    previous_close, received_ids, last_row = {}, [], {}
    delayed = 0
    for row in read_csv(demo.evaluation_dir / "simulation.csv"):
        sku = row["sku"]
        opening, receipt, available, demand, sold, closing = [int(row[k]) for k in
            ("opening_units", "receipt_units", "available_units", "latent_demand_units", "units_sold", "closing_units")]
        assert min(opening, receipt, available, demand, sold, closing) >= 0
        if sku in previous_close:
            assert previous_close[sku] == opening
        assert opening + receipt == available
        assert available - sold == closing
        assert sold <= available
        assert sold == (min(demand, available) if row["store_open"] == "true" else 0)
        sale = sales[(sku, row["date"])]
        assert sale.units_sold == sold
        assert sale.fully_in_stock == (sale.store_open and available > sold)
        po_ids = row["receipt_po_ids"].split("|") if row["receipt_po_ids"] else []
        assert sum(orders[p].ordered_units for p in po_ids) == receipt
        for po_id in po_ids:
            po = orders[po_id]
            assert po.status == "received"
            assert po.actual_arrival_date.isoformat() == row["date"]
            assert po.sku == sku
            delayed += po.actual_arrival_date > po.expected_arrival_date
            received_ids.append(po_id)
        previous_close[sku], last_row[sku] = closing, row
    assert len(received_ids) == len(set(received_ids))
    assert set(received_ids) == {p.po_id for p in orders.values() if p.status == "received"}
    for snapshot in batch.tables["inventory.csv"]:
        assert snapshot.on_hand_units == previous_close[snapshot.sku]
        assert snapshot.reserved_units == 0
    for po in orders.values():
        assert po.actual_arrival_date is None or po.actual_arrival_date <= batch.end_date
        assert po.status == "received" or po.actual_arrival_date is None
    truth = {r["po_id"]: r for r in read_csv(demo.evaluation_dir / "order_truth.csv")}
    for po_id, row in truth.items():
        po = orders[po_id]
        if row["cancelled"] == "true":
            assert po.status == "cancelled" and po_id not in received_ids
        elif date.fromisoformat(row["planned_actual_arrival_date"]) > batch.end_date:
            assert po.status == "open" and po.actual_arrival_date is None
        else:
            assert po.status == "received"
    assert delayed > 0
    quality = demo.manifest["validation_summary"]
    assert 0 < quality["stockout_affected_sku_days"] < 7300 * 0.25
    assert quality["closed_store_calendar_days"] > 0
    assert quality["purchase_orders"]["cancelled"] > 0


def test_hidden_truth_never_in_operational_contract(demo):
    operational_files = {p.name for p in demo.operational_dir.iterdir()}
    assert operational_files == set(CONTRACTS) | {"manifest.json"}
    for filename in CONTRACTS:
        header = (demo.operational_dir / filename).read_text().splitlines()[0]
        assert "latent" not in header and "planned_actual" not in header
    assert demo.evaluation_dir.parent.name == "evaluation_only"
    assert (demo.evaluation_dir / "simulation.csv").is_file()


def test_historical_policy_estimate_uses_only_observed_uncensored_history(demo):
    history = {}
    for row in read_csv(demo.evaluation_dir / "simulation.csv"):
        observations = history.setdefault(row["sku"], [])
        if row["fully_in_stock"] == "true":
            observations.append(int(row["units_sold"]))
        if observations:
            expected = sum(observations[-28:]) / len(observations[-28:])
            assert abs(float(row["policy_demand_estimate"]) - expected) <= 0.00000051


def test_short_custom_calendar(settings):
    generated = generate_demo(settings, GeneratorConfig(seed=1, days=1, sku_count=1, start_date=date(2020, 2, 29)))
    batch = read_batch(generated.operational_dir)
    assert batch.start_date == batch.end_date == date(2020, 2, 29)
