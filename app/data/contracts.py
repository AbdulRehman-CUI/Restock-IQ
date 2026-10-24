"""CSV contracts. Money rejects sub-cent precision; FX retains six decimals."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator
from app.core.enums import Currency, OrderStatus


def integer(value):
    if isinstance(value, bool) or not re.fullmatch(r"[+-]?\d+", str(value)):
        raise ValueError("Expected whole units (integer)")
    return int(value)


def decimal_at(scale):
    def parse(value):
        try:
            result = Decimal(str(value))
            if not result.is_finite() or abs(result) > Decimal("100000000000"):
                raise ValueError("Expected a finite decimal of magnitude at most 100000000000")
            if result != result.quantize(Decimal(10) ** -scale):
                raise ValueError(f"Expected at most {scale} decimal places")
        except InvalidOperation as exc:
            raise ValueError("Expected a valid decimal") from exc
        return result
    return parse


def boolean(value):
    if value in (True, "true", "1"):
        return True
    if value in (False, "false", "0"):
        return False
    raise ValueError("Use true/false or 1/0 for CSV booleans")


def iso_date(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Use ISO YYYY-MM-DD dates")
    return value


def iso_timestamp(value):
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:", value):
        raise ValueError("Use an ISO 8601 timestamp with a timezone offset")
    return value


Whole = Annotated[int, BeforeValidator(integer), Field(ge=0, le=2_000_000_000)]
PositiveWhole = Annotated[Whole, Field(gt=0)]
Money = Annotated[Decimal, BeforeValidator(decimal_at(2)), Field(ge=0)]
PositiveMoney = Annotated[Money, Field(gt=0)]
Fraction = Annotated[Decimal, BeforeValidator(decimal_at(4)), Field(ge=0, le=Decimal("0.80"))]
FX = Annotated[Decimal, BeforeValidator(decimal_at(6)), Field(gt=0)]
Flag = Annotated[bool, BeforeValidator(boolean)]
Text = Annotated[str, Field(min_length=1, max_length=200)]
ISODate = Annotated[date, BeforeValidator(iso_date)]
ISOTimestamp = Annotated[datetime, BeforeValidator(iso_timestamp)]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, validate_default=True)


class Product(Record):
    sku: Text
    name: Text
    category: Text
    supplier_id: Text
    current_price_pkr: PositiveMoney
    historical_unit_cost_pkr: Money
    target_margin: Fraction = Decimal("0.20")
    default_lead_days: Annotated[PositiveWhole, Field(le=30)]
    safety_days: Whole = 2
    review_days: PositiveWhole = 7
    moq: PositiveWhole = 1
    pack_size: PositiveWhole = 1


class DailySale(Record):
    date: ISODate
    sku: Text
    units_sold: Whole
    selling_price_pkr: PositiveMoney
    promo_active: Flag
    fully_in_stock: Flag
    store_open: Flag

    @model_validator(mode="after")
    def closed_store(self):
        if not self.store_open and (self.units_sold or self.fully_in_stock):
            raise ValueError("Closed days require units_sold=0 and fully_in_stock=false")
        return self


class Inventory(Record):
    sku: Text
    snapshot_date: ISODate
    on_hand_units: Whole
    reserved_units: Whole = 0

    @model_validator(mode="after")
    def reservations(self):
        if self.reserved_units > self.on_hand_units:
            raise ValueError("reserved_units cannot exceed on_hand_units")
        return self


class SupplierQuote(Record):
    quote_id: Text
    sku: Text
    supplier_id: Text
    observed_at: ISOTimestamp
    currency: Currency
    unit_price: PositiveMoney
    freight_unit_pkr: Money
    other_unit_pkr: Money
    valid_until: ISODate | None = None

    @model_validator(mode="after")
    def aware(self):
        if self.observed_at.utcoffset() is None:
            raise ValueError("observed_at requires a timezone offset")
        from zoneinfo import ZoneInfo
        if self.valid_until and self.valid_until < self.observed_at.astimezone(ZoneInfo("Asia/Karachi")).date():
            raise ValueError("valid_until cannot precede observed_at business date")
        return self


class PurchaseOrder(Record):
    po_id: Text
    sku: Text
    supplier_id: Text
    ordered_at: ISODate
    ordered_units: PositiveWhole
    expected_arrival_date: ISODate
    actual_arrival_date: ISODate | None = None
    unit_price: PositiveMoney
    currency: Currency
    status: OrderStatus

    @model_validator(mode="after")
    def chronology(self):
        if self.expected_arrival_date < self.ordered_at:
            raise ValueError("expected_arrival_date cannot precede ordered_at")
        if self.actual_arrival_date and self.actual_arrival_date < self.ordered_at:
            raise ValueError("actual_arrival_date cannot precede ordered_at")
        if (self.status == OrderStatus.RECEIVED) != (self.actual_arrival_date is not None):
            raise ValueError("Only received orders must have an actual_arrival_date")
        return self


class FXRate(Record):
    date: ISODate
    currency: Currency
    pkr_per_unit: FX
    source: Text

    @model_validator(mode="after")
    def pkr_identity(self):
        if self.currency == Currency.PKR and self.pkr_per_unit != Decimal(1):
            raise ValueError("PKR pkr_per_unit must equal 1")
        return self


CONTRACTS = {"products.csv": Product, "daily_sales.csv": DailySale,
             "inventory.csv": Inventory, "supplier_quotes.csv": SupplierQuote,
             "purchase_orders.csv": PurchaseOrder, "fx_rates.csv": FXRate}

KEYS = {"products.csv": ("sku",), "daily_sales.csv": ("sku", "date"),
        "inventory.csv": ("sku",), "supplier_quotes.csv": ("quote_id",),
        "purchase_orders.csv": ("po_id",), "fx_rates.csv": ("date", "currency")}
