from datetime import date
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class TrainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: UUID
    seed: int = Field(default=42, ge=0, le=2**32 - 1)


class ForecastRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: UUID
    model_id: UUID
    origin: date
    horizon_days: int = Field(ge=1, le=30)


class ModelResponse(BaseModel):
    id: UUID
    dataset_id: UUID
    job_id: UUID | None
    parent_model_id: UUID | None
    role: Literal["evaluation", "final_inference"]
    status: Literal["running", "completed", "failed"]
    training_cutoff: date
    feature_version: str
    selected_method: str
    seed: int
    created_at: str
    artifact_path: str | None
    artifact_hash: str | None
    metadata_hash: str | None
    details: dict[str, Any]
    error: str | None
    error_id: UUID | None


class DemandRow(BaseModel):
    sku: str
    date: date
    expected_units: float = Field(ge=0, allow_inf_nan=False)
    method: Literal["baseline", "random_forest"]
    fallback_applied: bool
    observation_count: int
    reason_codes: list[str]
    origin: date
    horizon_days: int
    model_id: UUID
    dataset_id: UUID


class ProductStatus(BaseModel):
    sku: str
    status: Literal["forecasted", "insufficient_history"]
    method: Literal["baseline", "random_forest"] | None
    fallback_applied: bool
    reason_codes: list[str]
    observation_count: int


class ForecastResponse(BaseModel):
    forecast_run_id: UUID
    dataset_id: UUID
    model_id: UUID
    origin: date
    horizon_days: int
    created_at: str
    product_status: list[ProductStatus]
    assumptions: dict[str, str]
    feature_version: str
    provenance: str
    forecast_duration_seconds: float
    row_count: int
    forecasted_products: int
    rows: list[DemandRow]
