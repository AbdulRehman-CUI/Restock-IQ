from dataclasses import dataclass
from datetime import date, timedelta
import csv
import hashlib
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError
from app.core.config import TIMEZONE
from app.core.errors import DatasetValidationError, Issues
from app.data.contracts import CONTRACTS, KEYS


@dataclass
class Batch:
    tables: dict[str, list[BaseModel]]
    hashes: dict[str, str]
    start_date: date
    end_date: date


def read_batch(directory: Path) -> Batch:
    issues = Issues()
    tables, hashes = {}, {}
    for filename, contract in CONTRACTS.items():
        path = directory / filename
        tables[filename] = []
        if not path.is_file():
            issues.add(filename, None, "file", "missing_file", "Provide this CSV file, including headers even when empty")
            continue
        hashes[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
        try:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle, strict=True)
                headers = reader.fieldnames or []
                required = {key for key, field in contract.model_fields.items() if field.is_required()}
                for field in sorted(required - set(headers)):
                    issues.add(filename, 1, field, "missing_column", "Required CSV column is missing")
                for field in sorted(set(headers) - set(contract.model_fields)):
                    issues.add(filename, 1, field, "unknown_column", "Column is outside the operational contract")
                if len(headers) != len(set(headers)):
                    issues.add(filename, 1, "header", "duplicate_column", "CSV column names must be unique")
                for row_num, raw in enumerate(reader, 2):
                    if None in raw or any(v is None for v in raw.values()):
                        issues.add(filename, row_num, "row", "column_count", "Row width must match header width")
                        continue
                    # Blank optional cells have the same meaning as omitted optional columns.
                    values = {k: v for k, v in raw.items() if v.strip() or k in required}
                    try:
                        tables[filename].append(contract.model_validate(values))
                    except (ValidationError, ArithmeticError) as exc:
                        if isinstance(exc, ValidationError):
                            for error in exc.errors():
                                issues.add(filename, row_num, ".".join(map(str, error["loc"])) or "row",
                                           error["type"], error["msg"])
                        else:
                            issues.add(filename, row_num, "row", "invalid_decimal", "Decimal exceeds supported precision or magnitude")
        except (UnicodeError, csv.Error) as exc:
            issues.add(filename, None, "file", "invalid_csv", f"Use a valid UTF-8 CSV: {exc}")
    # Relationship row positions are reliable only once all records parse successfully.
    if issues.total:
        raise DatasetValidationError(issues)
    start, end = validate_relationships(tables, issues)
    if issues.total:
        raise DatasetValidationError(issues)
    return Batch(tables, hashes, start, end)


def validate_relationships(tables, issues):
    for filename, records in tables.items():
        seen = set()
        for row, record in enumerate(records, 2):
            key = tuple(getattr(record, f) for f in KEYS[filename])
            if key in seen:
                issues.add(filename, row, ",".join(KEYS[filename]), "duplicate_record", f"Duplicate key {key}")
            seen.add(key)
    products = {p.sku: p for p in tables["products.csv"]}
    if not products:
        issues.add("products.csv", None, "sku", "empty_products", "At least one product is required")
    sales = tables["daily_sales.csv"]
    if not sales:
        issues.add("daily_sales.csv", None, "date", "empty_sales", "Provide a complete product/day sales calendar")
    dates = [s.date for s in sales]
    start, end = (min(dates), max(dates)) if dates else (date.min, date.min)
    for filename, records in tables.items():
        if filename in ("products.csv", "fx_rates.csv"):
            continue
        for row, record in enumerate(records, 2):
            if record.sku not in products:
                issues.add(filename, row, "sku", "unknown_product", f"Unknown SKU {record.sku}")
            elif hasattr(record, "supplier_id") and record.supplier_id != products[record.sku].supplier_id:
                issues.add(filename, row, "supplier_id", "supplier_mismatch", "Use the product's primary supplier_id")
            if filename == "inventory.csv" and record.snapshot_date != end:
                issues.add(filename, row, "snapshot_date", "snapshot_origin_mismatch", f"All snapshots must equal final sales date {end}")
            if filename == "supplier_quotes.csv" and record.observed_at.astimezone(ZoneInfo(TIMEZONE)).date() > end:
                issues.add(filename, row, "observed_at", "future_information", "Quote was not known at the end-of-day origin")
            if filename == "purchase_orders.csv":
                for field in ("ordered_at", "actual_arrival_date"):
                    value = getattr(record, field)
                    if value and value > end:
                        issues.add(filename, row, field, "future_information", "Do not expose future ordering or actual receipt information")
    for row, rate in enumerate(tables["fx_rates.csv"], 2):
        if rate.date > end:
            issues.add("fx_rates.csv", row, "date", "future_information", "FX observation is after the dataset origin")
    inventory_skus = {i.sku for i in tables["inventory.csv"]}
    for sku in sorted(products.keys() - inventory_skus):
        issues.add("inventory.csv", None, "sku", "missing_snapshot", f"Provide final inventory for {sku}")
    # Compare sets, not row counts: duplicates never compensate for missing days.
    actual = {(s.sku, s.date) for s in sales}
    calendar_flags = {}
    for row, sale in enumerate(sales, 2):
        if sale.date in calendar_flags and calendar_flags[sale.date] != sale.store_open:
            issues.add("daily_sales.csv", row, "store_open", "inconsistent_store_calendar",
                       "All products share the same single-location store calendar")
        calendar_flags[sale.date] = sale.store_open
    day_count = (end - start).days + 1
    # Iterate the calendar without allocating an unbounded cartesian product.
    for sku in sorted(products):
        missing_count = day_count - sum(key_sku == sku for key_sku, _ in actual)
        emitted = 0
        for offset in range(day_count):
            if len(issues.items) >= issues.limit or emitted == missing_count:
                break
            day = start + timedelta(days=offset)
            if (sku, day) not in actual:
                issues.add("daily_sales.csv", None, "date", "missing_sales_day",
                           f"Missing {sku} on {day}; provide an explicit open/closed day, never assume zero")
                emitted += 1
        issues.total += missing_count - emitted
    return start, end
