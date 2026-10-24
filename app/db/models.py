"""Dataset-scoped entities; scaled integer columns preserve Decimal exactly."""
from decimal import Decimal
from sqlalchemy import (JSON, Boolean, CheckConstraint, Date, Float, ForeignKey, ForeignKeyConstraint,
                        Integer, String, UniqueConstraint, DDL, event)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class ScaledDecimal(TypeDecorator):
    impl = Integer
    cache_ok = True

    def __init__(self, scale=2, **kwargs):
        self.scale = scale
        super().__init__(**kwargs)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        scaled = Decimal(str(value)) * 10 ** self.scale
        if not scaled.is_finite() or scaled != scaled.to_integral_value():
            raise ValueError("Decimal precision would be lost")
        return int(scaled)

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(value).scaleb(-self.scale)


class Base(DeclarativeBase):
    pass


class Dataset(Base):
    __tablename__ = "datasets"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    provenance: Mapped[str] = mapped_column(String)
    start_date: Mapped[object] = mapped_column(Date)
    end_date: Mapped[object] = mapped_column(Date)
    manifest: Mapped[dict] = mapped_column(JSON)
    quality: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (CheckConstraint("provenance IN ('synthetic','uploaded')"),)


class Scoped:
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), primary_key=True)


class Product(Scoped, Base):
    __tablename__ = "products"
    sku: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String)
    supplier_id: Mapped[str] = mapped_column(String)
    current_price_pkr: Mapped[Decimal] = mapped_column(ScaledDecimal())
    historical_unit_cost_pkr: Mapped[Decimal] = mapped_column(ScaledDecimal())
    target_margin: Mapped[Decimal] = mapped_column(ScaledDecimal(4))
    default_lead_days: Mapped[int] = mapped_column(Integer)
    safety_days: Mapped[int] = mapped_column(Integer)
    review_days: Mapped[int] = mapped_column(Integer)
    moq: Mapped[int] = mapped_column(Integer)
    pack_size: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint("dataset_id", "sku", "supplier_id"),
        CheckConstraint("current_price_pkr > 0 AND historical_unit_cost_pkr >= 0"),
        CheckConstraint("target_margin BETWEEN 0 AND 8000"),
        CheckConstraint("default_lead_days BETWEEN 1 AND 30 AND safety_days >= 0"),
        CheckConstraint("review_days > 0 AND moq > 0 AND pack_size > 0"),
    )


def product_fk():
    return ForeignKeyConstraint(["dataset_id", "sku"], ["products.dataset_id", "products.sku"])


def supplier_fk():
    return ForeignKeyConstraint(["dataset_id", "sku", "supplier_id"],
                               ["products.dataset_id", "products.sku", "products.supplier_id"])


class DailySale(Scoped, Base):
    __tablename__ = "daily_sales"
    sku: Mapped[str] = mapped_column(String, primary_key=True)
    date: Mapped[object] = mapped_column(Date, primary_key=True)
    units_sold: Mapped[int] = mapped_column(Integer)
    selling_price_pkr: Mapped[Decimal] = mapped_column(ScaledDecimal())
    promo_active: Mapped[bool] = mapped_column(Boolean)
    fully_in_stock: Mapped[bool] = mapped_column(Boolean)
    store_open: Mapped[bool] = mapped_column(Boolean)
    __table_args__ = (product_fk(), CheckConstraint("units_sold >= 0 AND selling_price_pkr > 0"),
                      CheckConstraint("store_open OR (units_sold = 0 AND NOT fully_in_stock)"))


class InventorySnapshot(Scoped, Base):
    __tablename__ = "inventory_snapshots"
    sku: Mapped[str] = mapped_column(String, primary_key=True)
    snapshot_date: Mapped[object] = mapped_column(Date)
    on_hand_units: Mapped[int] = mapped_column(Integer)
    reserved_units: Mapped[int] = mapped_column(Integer)
    __table_args__ = (product_fk(), CheckConstraint("on_hand_units >= 0 AND reserved_units BETWEEN 0 AND on_hand_units"))


class SupplierQuote(Scoped, Base):
    __tablename__ = "supplier_quotes"
    quote_id: Mapped[str] = mapped_column(String, primary_key=True)
    sku: Mapped[str] = mapped_column(String)
    supplier_id: Mapped[str] = mapped_column(String)
    observed_at: Mapped[str] = mapped_column(String)
    currency: Mapped[str] = mapped_column(String)
    unit_price: Mapped[Decimal] = mapped_column(ScaledDecimal())
    freight_unit_pkr: Mapped[Decimal] = mapped_column(ScaledDecimal())
    other_unit_pkr: Mapped[Decimal] = mapped_column(ScaledDecimal())
    valid_until: Mapped[object | None] = mapped_column(Date, nullable=True)
    __table_args__ = (supplier_fk(), CheckConstraint("currency IN ('PKR','USD')"),
                      CheckConstraint("unit_price > 0 AND freight_unit_pkr >= 0 AND other_unit_pkr >= 0"))


class PurchaseOrder(Scoped, Base):
    __tablename__ = "purchase_orders"
    po_id: Mapped[str] = mapped_column(String, primary_key=True)
    sku: Mapped[str] = mapped_column(String)
    supplier_id: Mapped[str] = mapped_column(String)
    ordered_at: Mapped[object] = mapped_column(Date)
    ordered_units: Mapped[int] = mapped_column(Integer)
    expected_arrival_date: Mapped[object] = mapped_column(Date)
    actual_arrival_date: Mapped[object | None] = mapped_column(Date, nullable=True)
    unit_price: Mapped[Decimal] = mapped_column(ScaledDecimal())
    currency: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    __table_args__ = (
        supplier_fk(), CheckConstraint("ordered_units > 0 AND unit_price > 0"),
        CheckConstraint("currency IN ('PKR','USD')"),
        CheckConstraint("status IN ('open','received','cancelled')"),
        CheckConstraint("expected_arrival_date >= ordered_at"),
        CheckConstraint("(status = 'received' AND actual_arrival_date IS NOT NULL AND actual_arrival_date >= ordered_at) OR (status != 'received' AND actual_arrival_date IS NULL)"),
    )


class FXRate(Scoped, Base):
    __tablename__ = "fx_rates"
    date: Mapped[object] = mapped_column(Date, primary_key=True)
    currency: Mapped[str] = mapped_column(String, primary_key=True)
    pkr_per_unit: Mapped[Decimal] = mapped_column(ScaledDecimal(6))
    source: Mapped[str] = mapped_column(String)
    __table_args__ = (CheckConstraint("pkr_per_unit > 0"), CheckConstraint("currency IN ('PKR','USD')"),
                      CheckConstraint("currency != 'PKR' OR pkr_per_unit = 1000000"))


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String)
    active_key: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)
    dataset_id: Mapped[str | None] = mapped_column(ForeignKey("datasets.id"), nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    error_id: Mapped[str | None] = mapped_column(String, nullable=True)
    configuration: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (CheckConstraint("status IN ('queued','running','completed','failed')"),
                      CheckConstraint("progress BETWEEN 0 AND 100"))


TABLES = {"products.csv": Product, "daily_sales.csv": DailySale,
          "inventory.csv": InventorySnapshot, "supplier_quotes.csv": SupplierQuote,
          "purchase_orders.csv": PurchaseOrder, "fx_rates.csv": FXRate}


class ModelRun(Base):
    __tablename__ = "model_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True, index=True)
    parent_model_id: Mapped[str | None] = mapped_column(ForeignKey("model_runs.id"), nullable=True)
    role: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    training_cutoff: Mapped[object] = mapped_column(Date)
    feature_version: Mapped[str] = mapped_column(String)
    selected_method: Mapped[str] = mapped_column(String)
    seed: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String)
    artifact_path: Mapped[str | None] = mapped_column(String, nullable=True)
    artifact_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    details: Mapped[dict] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    error_id: Mapped[str | None] = mapped_column(String, nullable=True)
    __table_args__ = (UniqueConstraint("id", "dataset_id"),
                      CheckConstraint("role IN ('evaluation','final_inference')"),
                      CheckConstraint("status IN ('running','completed','failed')"))


class ForecastRun(Base):
    __tablename__ = "forecast_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"))
    model_id: Mapped[str] = mapped_column(String)
    origin: Mapped[object] = mapped_column(Date)
    horizon_days: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String)
    details: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (ForeignKeyConstraint(["model_id", "dataset_id"], ["model_runs.id", "model_runs.dataset_id"]),
                      CheckConstraint("horizon_days BETWEEN 1 AND 30"))


class ForecastRow(Base):
    __tablename__ = "forecast_rows"
    run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id"), primary_key=True)
    sku: Mapped[str] = mapped_column(String, primary_key=True)
    date: Mapped[object] = mapped_column(Date, primary_key=True)
    expected_units: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String)
    fallback_applied: Mapped[bool] = mapped_column(Boolean)
    observation_count: Mapped[int] = mapped_column(Integer)
    reason_codes: Mapped[list] = mapped_column(JSON)
    __table_args__ = (CheckConstraint("expected_units >= 0"),
                      CheckConstraint("method IN ('baseline','random_forest')"))


class Scenario(Base):
    __tablename__ = "scenarios"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"))
    forecast_run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id"))
    name: Mapped[str] = mapped_column(String)
    assumptions: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String)


for operation in ("UPDATE", "DELETE"):
    event.listen(Scenario.__table__, "after_create", DDL(
        f"CREATE TRIGGER scenarios_no_{operation.lower()} BEFORE {operation} ON scenarios "
        "BEGIN SELECT RAISE(ABORT, 'Scenarios are immutable; create a new scenario'); END"
    ).execute_if(dialect="sqlite"))


class RecommendationRun(Base):
    __tablename__ = "recommendation_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"))
    forecast_run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id"))
    model_id: Mapped[str] = mapped_column(ForeignKey("model_runs.id"))
    scenario_id: Mapped[str | None] = mapped_column(ForeignKey("scenarios.id"), nullable=True)
    origin: Mapped[object] = mapped_column(Date)
    horizon_days: Mapped[int] = mapped_column(Integer)
    calculation_version: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)
    details: Mapped[dict] = mapped_column(JSON)


class RecommendationProduct(Base):
    __tablename__ = "recommendation_products"
    run_id: Mapped[str] = mapped_column(ForeignKey("recommendation_runs.id"), primary_key=True)
    sku: Mapped[str] = mapped_column(String, primary_key=True)
    result: Mapped[dict] = mapped_column(JSON)


class RecommendationAction(Base):
    __tablename__ = "recommendation_actions"
    request_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    sku: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    timestamp: Mapped[str] = mapped_column(String)
    note: Mapped[str] = mapped_column(String)
    __table_args__ = (
        ForeignKeyConstraint(["run_id", "sku"], ["recommendation_products.run_id", "recommendation_products.sku"]),
        CheckConstraint("action IN ('reviewed','rejected')"),
    )


for operation in ("UPDATE", "DELETE"):
    event.listen(RecommendationAction.__table__, "after_create", DDL(
        f"CREATE TRIGGER actions_no_{operation.lower()} BEFORE {operation} ON recommendation_actions "
        "BEGIN SELECT RAISE(ABORT, 'Recommendation actions are append-only'); END"
    ).execute_if(dialect="sqlite"))
