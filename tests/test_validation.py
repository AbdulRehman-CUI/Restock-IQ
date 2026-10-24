from decimal import Decimal
import pytest
from app.core.errors import DatasetValidationError
from app.data.contracts import Product
from app.services.validation import read_batch
from conftest import mutate_csv


def error_payload(directory):
    with pytest.raises(DatasetValidationError) as exc:
        read_batch(directory)
    return exc.value.issues.payload()


@pytest.mark.parametrize("filename,field,value,code", [
    ("daily_sales.csv", "sku", "unknown", "unknown_product"),
    ("supplier_quotes.csv", "supplier_id", "unknown", "supplier_mismatch"),
    ("fx_rates.csv", "pkr_per_unit", "0", "greater_than"),
    ("fx_rates.csv", "pkr_per_unit", "NaN", "value_error"),
    ("products.csv", "current_price_pkr", "not-a-price", "value_error"),
    ("fx_rates.csv", "currency", "EUR", "enum"),
    ("products.csv", "target_margin", "20", "less_than_equal"),
    ("products.csv", "target_margin", "-0.1", "greater_than_equal"),
    ("products.csv", "current_price_pkr", "12.345", "value_error"),
    ("daily_sales.csv", "units_sold", "-1", "greater_than_equal"),
    ("daily_sales.csv", "units_sold", "1.5", "value_error"),
    ("daily_sales.csv", "promo_active", "yes", "value_error"),
    ("inventory.csv", "snapshot_date", "2025-10-16", "snapshot_origin_mismatch"),
    ("inventory.csv", "reserved_units", "2000000", "value_error"),
    ("supplier_quotes.csv", "observed_at", "2025-10-16T08:00:00", "value_error"),
    ("supplier_quotes.csv", "observed_at", "123", "value_error"),
    ("supplier_quotes.csv", "observed_at", "2030-01-01T08:00:00+05:00", "value_error"),
    ("fx_rates.csv", "date", "2030-01-01", "future_information"),
    ("purchase_orders.csv", "actual_arrival_date", "2030-01-01", "value_error"),
    ("purchase_orders.csv", "expected_arrival_date", "2000-01-01", "value_error"),
])
def test_invalid_records(batch_dir, filename, field, value, code):
    mutate_csv(batch_dir, filename, lambda rows: rows[0].update({field: value}))
    payload = error_payload(batch_dir)
    assert any(e["code"] == code and e["file"] == filename for e in payload["errors"]), payload
    assert all(set(e) == {"file", "row", "field", "code", "message"} for e in payload["errors"])


@pytest.mark.parametrize("filename", ["products.csv", "daily_sales.csv", "inventory.csv", "supplier_quotes.csv", "purchase_orders.csv", "fx_rates.csv"])
def test_duplicate_keys(batch_dir, filename):
    mutate_csv(batch_dir, filename, lambda rows: rows.append(dict(rows[0])))
    assert any(e["code"] == "duplicate_record" for e in error_payload(batch_dir)["errors"])


def test_missing_day_not_zero_filled(batch_dir):
    mutate_csv(batch_dir, "daily_sales.csv", lambda rows: rows.pop(1))
    assert any(e["code"] == "missing_sales_day" for e in error_payload(batch_dir)["errors"])


def test_received_future_actual_arrival_is_rejected(batch_dir):
    def expose_future(rows):
        next(row for row in rows if row["status"] == "received")["actual_arrival_date"] = "2030-01-01"
    mutate_csv(batch_dir, "purchase_orders.csv", expose_future)
    assert any(e["code"] == "future_information" for e in error_payload(batch_dir)["errors"])


def test_received_order_requires_actual_arrival(batch_dir):
    def remove_actual(rows):
        next(row for row in rows if row["status"] == "received")["actual_arrival_date"] = ""
    mutate_csv(batch_dir, "purchase_orders.csv", remove_actual)
    assert any(e["code"] == "value_error" for e in error_payload(batch_dir)["errors"])


def test_store_calendar_is_shared(batch_dir):
    mutate_csv(batch_dir, "daily_sales.csv", lambda rows: rows[60].update(store_open="false", units_sold="0", fully_in_stock="false"))
    assert any(e["code"] == "inconsistent_store_calendar" for e in error_payload(batch_dir)["errors"])


def test_missing_snapshot_and_file(batch_dir):
    mutate_csv(batch_dir, "inventory.csv", lambda rows: rows.pop())
    assert any(e["code"] == "missing_snapshot" for e in error_payload(batch_dir)["errors"])
    (batch_dir / "fx_rates.csv").unlink()
    assert error_payload(batch_dir)["errors"][0]["code"] == "missing_file"


def test_error_volume_limit_and_full_count(batch_dir):
    def duplicate(rows):
        rows.extend([dict(rows[0]) for _ in range(150)])
    mutate_csv(batch_dir, "daily_sales.csv", duplicate)
    result = error_payload(batch_dir)
    assert result["error_count"] == 150
    assert len(result["errors"]) == 100 and result["truncated"]


def test_optional_defaults(batch_dir):
    product = Product(sku="x", name="Example", category="cables", supplier_id="s", current_price_pkr="1.00",
                      historical_unit_cost_pkr="0", default_lead_days="1")
    assert product.target_margin == Decimal("0.20")
    assert (product.safety_days, product.review_days, product.moq, product.pack_size) == (2, 7, 1, 1)
    mutate_csv(batch_dir, "inventory.csv", lambda rows: [r.update(reserved_units="") for r in rows])
    assert all(i.reserved_units == 0 for i in read_batch(batch_dir).tables["inventory.csv"])


def test_unknown_truth_column_is_rejected(batch_dir):
    path = batch_dir / "daily_sales.csv"
    lines = path.read_text().splitlines()
    path.write_text("\n".join([lines[0] + ",latent_demand_units", *[line + ",10" for line in lines[1:]]]) + "\n")
    assert any(e["code"] == "unknown_column" for e in error_payload(batch_dir)["errors"])
