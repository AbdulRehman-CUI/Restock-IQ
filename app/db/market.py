"""Current market inputs live apart from immutable historical dataset contracts."""
from sqlalchemy import JSON, ForeignKey, String, Integer, LargeBinary, UniqueConstraint, CheckConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.db.models import Base, ScaledDecimal, supplier_fk


class SchemaVersion(Base):
    __tablename__ = "schema_versions"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[str] = mapped_column(String)


class FXObservation(Base):
    __tablename__ = "market_fx_observations"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    provider: Mapped[str] = mapped_column(String)
    observed_at: Mapped[str] = mapped_column(String, index=True)
    fetched_at: Mapped[str] = mapped_column(String)
    next_update_at: Mapped[str] = mapped_column(String)
    rate: Mapped[object] = mapped_column(ScaledDecimal(6))
    source: Mapped[str] = mapped_column(String)
    __table_args__ = (UniqueConstraint("provider", "observed_at"), CheckConstraint("rate > 0"))


class FXRefresh(Base):
    __tablename__ = "market_fx_refresh"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    lease_until: Mapped[str] = mapped_column(String, default="")
    next_attempt_at: Mapped[str] = mapped_column(String, default="")
    last_attempt_at: Mapped[str | None] = mapped_column(String, nullable=True)
    last_success_at: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)


class MarketSettings(Base):
    __tablename__ = "market_settings"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), index=True)
    created_at: Mapped[str] = mapped_column(String)
    supplied_by: Mapped[str] = mapped_column(String)
    values: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (Index("market_settings_latest", "dataset_id", "created_at", "id"),)


class MarketQuote(Base):
    __tablename__ = "market_quotes"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String)
    sku: Mapped[str] = mapped_column(String)
    supplier_id: Mapped[str] = mapped_column(String)
    observed_at: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)
    supplied_by: Mapped[str] = mapped_column(String)
    reconfirms_id: Mapped[str | None] = mapped_column(ForeignKey("market_quotes.id"), nullable=True)
    values: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (supplier_fk(), Index("market_quote_latest", "dataset_id", "sku", "observed_at", "created_at"))


class PricingAssessment(Base):
    __tablename__ = "current_pricing_assessments"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), index=True)
    created_at: Mapped[str] = mapped_column(String, index=True)
    fingerprint: Mapped[str] = mapped_column(String)
    result: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("dataset_id", "fingerprint"),)


class QuoteUpload(Base):
    __tablename__ = "market_quote_uploads"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"))
    filename: Mapped[str] = mapped_column(String)
    expected_bytes: Mapped[int] = mapped_column(Integer)
    total_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String)
    expires_at: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default="open")
    preview: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    __table_args__ = (CheckConstraint("expected_bytes > 0 AND total_bytes >= 0"),)


class QuoteChunk(Base):
    __tablename__ = "market_quote_chunks"
    upload_id: Mapped[str] = mapped_column(ForeignKey("market_quote_uploads.id", ondelete="CASCADE"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[bytes] = mapped_column(LargeBinary)


MARKET_TABLES = [FXObservation.__table__, FXRefresh.__table__, MarketSettings.__table__, MarketQuote.__table__,
                 PricingAssessment.__table__, QuoteUpload.__table__, QuoteChunk.__table__]
IMMUTABLE_TABLES = [FXObservation.__table__, MarketSettings.__table__, MarketQuote.__table__, PricingAssessment.__table__]
