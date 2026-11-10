from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from pydantic import Field, AwareDatetime, model_validator
from app.data.contracts import Record, Text, Money, PositiveMoney, PositiveWhole, FX, Fraction
from app.services.pricing import PricingSettings, Percent
from app.api.schemas.decisions import PriceResult


class RateSettings(Record):
    mode: Literal["reference_spread", "manual_bank"] = "reference_spread"
    spread_percent: Percent = Decimal("0")
    manual_rate: FX | None = None
    manual_observed_at: AwareDatetime | None = None
    manual_valid_until: AwareDatetime | None = None
    manual_source: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=1000)
    real_fx_opt_in: bool = False
    auto_assess: bool = False
    pricing: PricingSettings = Field(default_factory=PricingSettings)
    target_margin: Fraction | None = None
    order_quantity: PositiveWhole | None = None
    payment_fee_total_pkr: Money = Decimal("0")
    payment_fee_sku: Text | None = None

    @model_validator(mode="after")
    def valid_mode(self):
        now = datetime.now(timezone.utc)
        if self.mode == "manual_bank":
            if not self.manual_rate or not self.manual_observed_at or not self.manual_source.strip():
                raise ValueError("A bank selling rate requires its source and observation timestamp")
            if self.manual_observed_at > now:
                raise ValueError("Manual rate cannot be observed in the future")
            if self.manual_valid_until and self.manual_valid_until <= self.manual_observed_at:
                raise ValueError("Rate expiry must follow its observation")
            if self.spread_percent:
                raise ValueError("Manual bank selling rates must not also include a reference-rate spread")
        elif any((self.manual_rate, self.manual_observed_at, self.manual_valid_until, self.manual_source)):
            raise ValueError("Clear manual rate fields when selecting reference plus spread")
        if self.payment_fee_total_pkr and (not self.order_quantity or not self.payment_fee_sku):
            raise ValueError("A fixed payment fee needs an explicit SKU and batch quantity for equal-per-unit allocation")
        return self


class QuoteInput(Record):
    sku: Text
    supplier_id: Text
    currency: Literal["USD", "PKR"]
    unit_price: PositiveMoney
    observed_at: AwareDatetime
    valid_until: AwareDatetime | None = None
    min_quantity: PositiveWhole = 1
    max_quantity: PositiveWhole | None = None
    unit_basis: Literal["unit", "pack"] = "unit"
    pack_units: PositiveWhole = 1
    freight_unit_pkr: Money | None = None
    other_unit_pkr: Money | None = None
    source_type: Literal["manual", "CSV", "XLSX"] = "manual"
    source_reference: str = Field(default="", max_length=1000)
    note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def compatible(self):
        if self.observed_at > datetime.now(timezone.utc):
            raise ValueError("Quote observations cannot be in the future")
        if self.valid_until and self.valid_until <= self.observed_at:
            raise ValueError("Quote expiry must follow its observation")
        if self.max_quantity and self.max_quantity < self.min_quantity:
            raise ValueError("Maximum quantity must be at least the minimum quantity")
        if self.unit_basis == "unit" and self.pack_units != 1:
            raise ValueError("A unit quote must use pack_units=1")
        if self.source_reference and not self.source_reference.startswith(("https://", "http://")):
            raise ValueError("Source reference must be an HTTP(S) link; it will never be fetched")
        return self


class ReconfirmQuote(Record):
    observed_at: AwareDatetime
    valid_until: AwareDatetime | None = None
    note: str = Field(default="", max_length=1000)


class AssessmentRequest(Record):
    sku: Text | None = None


class UploadStart(Record):
    filename: str = Field(min_length=1, max_length=200)
    size: int = Field(gt=0, le=12 * 1024 * 1024)


class ConfirmImport(Record):
    preview_hash: str = Field(min_length=64, max_length=64)
    confirm: Literal[True]


class FXRateView(Record):
    id: str
    provider: str
    observed_at: str
    fetched_at: str
    next_update_at: str
    rate: str
    source: str


class FXStatus(Record):
    status: Literal["fresh", "stale", "unavailable"]
    observation: FXRateView | None
    age_seconds: int | None
    last_attempt_at: str | None
    last_success_at: str | None
    next_attempt_at: str
    refresh_failed: bool
    error: str | None
    attribution: str
    attribution_url: str
    notice: str


class SettingsView(Record):
    id: str | None
    created_at: str | None
    supplied_by: str | None
    values: RateSettings


class QuoteView(Record):
    id: str
    dataset_id: str
    sku: str
    supplier_id: str
    observed_at: str
    created_at: str
    supplied_by: str
    reconfirms_id: str | None
    values: QuoteInput


class AssessmentQuote(Record):
    model_config = {"extra": "allow"}
    id: str
    confirmed: bool
    observed_at: str
    supplied_by: str
    values: dict


class CurrentProductView(Record):
    sku: str
    name: str
    status: Literal["available", "indicative", "unavailable"]
    quote: AssessmentQuote | None
    quote_age_days: int | None
    quote_validity: Literal["unknown", "dated", "expired"]
    quote_price_assumed_unchanged: bool
    effective_rate: Decimal | None
    components: dict
    replacement_cost_pkr: Decimal | None
    unrounded_replacement_cost_pkr: Decimal | None
    minimum_replacement_funding_price_pkr: Decimal | None
    unrounded_funding_shortfall_unit_pkr: Decimal | None
    pricing: PriceResult
    warnings: list[str]
    assumptions: list[str]
    funding_assurance: bool


class AssessmentView(Record):
    id: str
    dataset_id: str
    assessed_at: str
    calculation_version: str
    fingerprint: str
    label: str
    context: dict
    settings: dict
    fx: FXStatus
    rate_selection: dict
    products: list[CurrentProductView]
    assumptions: list[str]
    comparison: dict | None = None


class ComparisonView(Record):
    before_id: str
    after_id: str
    products: list[dict]


class UploadTicket(Record):
    id: str
    chunk_bytes: int
    expires_at: str


class ChunkReceipt(Record):
    received: int


class ImportFinding(Record):
    row: int | None
    message: str


class ImportPreview(Record):
    quotes: list[QuoteInput]
    findings: list[ImportFinding]
    error_count: int
    row_count: int
    can_confirm: bool
    preview_hash: str


class ImportReceipt(Record):
    id: str
    dataset_id: str
    quote_ids: list[str]
    count: int
    preview_hash: str


class AssessmentSummary(Record):
    id: str
    assessed_at: str
    label: str
    products: list[str]


class ProductAlert(Record):
    sku: str
    alerts: list[str]
    assumptions: list[str]


class ScheduledResult(Record):
    fx_status: str
    refresh_failed: bool
    assessment_ids: list[str]
