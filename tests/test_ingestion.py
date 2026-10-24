from decimal import Decimal
from unittest.mock import patch
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.core.errors import DatasetValidationError
from app.db.models import Base, Dataset, Product, DailySale, TABLES
from app.db.repositories import get_summary
from app.services.ingestion import import_dataset
from conftest import mutate_csv


def count(db, model):
    with db.sessions() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_default_import_summary_and_money(db, settings, demo):
    result = import_dataset(db, settings, demo.operational_dir, synthetic=True,
                            configuration=demo.manifest["generator_configuration"])
    assert result["row_counts"]["daily_sales"] == 7300
    assert result["product_count"] == 20
    assert result["sales_coverage"]["fraction"] == 1
    assert get_summary(db, result["dataset_id"]) == result
    for filename, model in TABLES.items():
        assert count(db, model) == demo.manifest["row_counts"][filename.removesuffix(".csv")]
    with db.sessions() as session:
        product = session.scalars(select(Product)).first()
        assert isinstance(product.current_price_pkr, Decimal)
        raw_price = session.execute(text("SELECT current_price_pkr FROM products LIMIT 1")).scalar()
        assert raw_price == product.current_price_pkr * 100
        assert session.execute(text("PRAGMA foreign_keys")).scalar() == 1
    assert "simulation" not in Base.metadata.tables and "latent_demand" not in Base.metadata.tables


def test_validation_failure_has_no_partial_dataset(db, settings, batch_dir):
    mutate_csv(batch_dir, "daily_sales.csv", lambda rows: rows[0].update(sku="unknown"))
    with pytest.raises(DatasetValidationError):
        import_dataset(db, settings, batch_dir)
    assert count(db, Dataset) == 0
    assert all(count(db, m) == 0 for m in TABLES.values())


def test_database_failure_rolls_back_existing_dataset_survives(db, settings, small_demo):
    existing = import_dataset(db, settings, small_demo.operational_dir)
    import app.services.ingestion as ingestion
    original = ingestion.add_dataset
    def fail_after_writes(*args):
        original(*args)
        raise RuntimeError("injected disk/write failure")
    with patch.object(ingestion, "add_dataset", fail_after_writes), pytest.raises(RuntimeError):
        import_dataset(db, settings, small_demo.operational_dir)
    assert count(db, Dataset) == 1
    assert count(db, DailySale) == 300
    assert get_summary(db, existing["dataset_id"]) == existing


def test_identical_uploads_are_isolated(db, settings, small_demo):
    first = import_dataset(db, settings, small_demo.operational_dir)
    second = import_dataset(db, settings, small_demo.operational_dir)
    assert first["dataset_id"] != second["dataset_id"]
    assert first["manifest"]["provenance"] == "uploaded"
    assert first["manifest"]["file_hashes"] == second["manifest"]["file_hashes"]
    assert count(db, DailySale) == 600
    with pytest.raises(IntegrityError):
        import_dataset(db, settings, small_demo.operational_dir, dataset_id=first["dataset_id"])
    assert count(db, DailySale) == 600


def test_db_rejects_foreign_key_and_duplicates(db, settings, small_demo):
    result = import_dataset(db, settings, small_demo.operational_dir)
    with pytest.raises(IntegrityError), db.transaction() as session:
        session.execute(text("UPDATE daily_sales SET sku='missing' WHERE dataset_id=:id"), {"id": result["dataset_id"]})
    with pytest.raises(IntegrityError), db.transaction() as session:
        session.execute(text("UPDATE fx_rates SET pkr_per_unit=0"))


def test_valid_schema_with_missing_costs_has_blocked_readiness(db, settings, batch_dir):
    mutate_csv(batch_dir, "supplier_quotes.csv", lambda rows: rows.clear())
    result = import_dataset(db, settings, batch_dir)
    assert result["schema_valid"]
    assert result["blocking_errors"] == []
    assert len(result["quotations"]["missing"]) == 5
    assert not result["readiness"]["current_cost_inputs_available"]


def test_stale_inputs_warning_and_missing_fx(db, settings, batch_dir):
    mutate_csv(batch_dir, "supplier_quotes.csv", lambda rows: rows.__setitem__(slice(None), [r for r in rows if "-0001" in r["quote_id"]]))
    mutate_csv(batch_dir, "fx_rates.csv", lambda rows: rows.__setitem__(slice(None), [r for r in rows if r["date"] == "2025-10-16"]))
    result = import_dataset(db, settings, batch_dir)
    assert len(result["quotations"]["stale"]) == 5
    assert result["exchange_rates"]["stale"] == ["USD"]
    assert result["readiness"]["current_cost_inputs_available"]
    mutate_csv(batch_dir, "fx_rates.csv", lambda rows: rows.__setitem__(slice(None), [r for r in rows if r["currency"] == "PKR"]))
    result = import_dataset(db, settings, batch_dir)
    assert result["exchange_rates"]["missing"] == ["USD"]
    assert not result["readiness"]["current_cost_inputs_available"]
