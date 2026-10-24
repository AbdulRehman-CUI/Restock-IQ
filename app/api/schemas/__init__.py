from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"]
    schema_version: str
    timezone: str
    reporting_currency: Literal["PKR"]


class JobAccepted(BaseModel):
    job_id: UUID
    status_url: str


class JobResponse(BaseModel):
    id: UUID
    status: Literal["queued", "running", "completed", "failed"]
    progress: int
    stage: str
    created_at: datetime
    updated_at: datetime
    dataset_id: UUID | None
    model_id: UUID | None = None
    error: str | None
    error_id: UUID | None
    configuration: dict[str, Any]


class QualitySummary(BaseModel):
    dataset_id: UUID
    manifest: dict[str, Any]
    schema_valid: bool
    blocking_errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    product_count: int
    date_range: dict[str, str]
    row_counts: dict[str, int]
    sales_coverage: dict[str, int | float]
    stockout_affected_sku_days: int
    closed_store_sku_days: int
    closed_store_calendar_days: int
    quotations: dict[str, list[str]]
    exchange_rates: dict[str, list[str]]
    purchase_orders: dict[str, int]
    inventory: dict[str, int]
    readiness: dict[str, Any]
    freshness_policy: dict[str, int]
