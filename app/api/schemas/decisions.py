from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from app.services.pricing import PricingSettings
from app.services.scenarios import ScenarioAssumptions


class ScenarioRequest(ScenarioAssumptions):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    dataset_id: UUID
    forecast_run_id: UUID
    name: str = Field(min_length=1, max_length=200)


class ScenarioResponse(BaseModel):
    id: UUID
    dataset_id: UUID
    forecast_run_id: UUID
    name: str
    assumptions: ScenarioAssumptions
    created_at: str
    hypothetical: bool
    notice: str


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: UUID
    forecast_run_id: UUID
    scenario_id: UUID | None = None
    pricing: PricingSettings = Field(default_factory=PricingSettings)


class Component(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: str
    warnings: list[str]


class CostResult(Component):
    status: Literal["available", "indicative", "unavailable"]
    replacement_cost_pkr: Decimal | None
    unrounded_replacement_cost_pkr: Decimal | None
    quote_id: str | None
    observed_at: str | None
    currency: str | None
    rate_date: date | None
    rate_source: str | None
    components: dict[str, Any]


class PriceResult(Component):
    status: Literal["available", "unavailable"]
    current_price_pkr: Decimal
    historical_unit_cost_pkr: Decimal
    historical_gross_difference_pkr: Decimal
    target_margin: Decimal
    current_replacement_margin: Decimal | None
    funding_shortfall_unit_pkr: Decimal | None
    unrounded_target_price_pkr: Decimal | None
    suggested_price_pkr: Decimal | None
    achievable_margin: Decimal | None
    price_change_pkr: Decimal | None
    absolute_price_change_pkr: Decimal | None
    price_change_pct: Decimal | None


class DailyProjection(BaseModel):
    date: date
    opening_units: Decimal = Field(ge=0, allow_inf_nan=False)
    receipt_units: int = Field(ge=0)
    expected_demand_units: Decimal = Field(ge=0, allow_inf_nan=False)
    expected_fulfilled_units: Decimal = Field(ge=0, allow_inf_nan=False)
    expected_unmet_units: Decimal = Field(ge=0, allow_inf_nan=False)
    closing_units: Decimal = Field(ge=0, allow_inf_nan=False)


class InventoryResult(Component):
    status: Literal["available", "unavailable"]
    daily: list[DailyProjection]
    first_shortage_date: date | None
    first_depletion_date: date | None
    shortage_status: str
    expected_unmet_units: Decimal | None
    quantity_status: str
    order_quantity: int | None
    order_required: bool
    supply_action_required: bool
    expedite_required: bool
    estimated_replenishment_cash_pkr: Decimal | None
    cash_status: str
    timing: dict[str, Any]


class ProductResult(BaseModel):
    sku: str
    name: str
    inputs: dict[str, Any]
    forecast: dict[str, Any]
    lead_time: dict[str, Any]
    costs: CostResult
    pricing: PriceResult
    inventory: InventoryResult
    reason_codes: list[str]
    explanations: list[str]


class RecommendationResponse(BaseModel):
    recommendation_run_id: UUID
    dataset_id: UUID
    forecast_run_id: UUID
    model_id: UUID
    scenario_id: UUID | None
    origin: date
    horizon_days: int
    calculation_version: str
    created_at: str
    effective_settings: dict[str, Any]
    scenario: dict[str, Any] | None
    provenance: str
    assumptions: list[str]
    summary: dict[str, Any]
    products: list[ProductResult]


class ComparisonResponse(BaseModel):
    base_run_id: UUID
    scenario_run_id: UUID
    forecast_run_id: UUID
    demand_unchanged: bool
    aggregate: dict[str, Any]
    products: list[dict[str, Any]]
