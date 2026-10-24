from datetime import date, datetime
from decimal import Decimal as D
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo
import pytest
from pydantic import ValidationError
from app.core.config import Settings
from app.data.contracts import SupplierQuote, FXRate
from app.services.costs import replacement_cost
from app.services.pricing import PricingSettings, price_recommendation
from app.services.scenarios import ScenarioAssumptions

ORIGIN = date(2026, 10, 15)


def product(**overrides):
    return NS(**{"sku": "A", "supplier_id": "SUP", "current_price_pkr": D("1200.00"),
        "historical_unit_cost_pkr": D("1000.00"), "target_margin": D("0.20"), "default_lead_days": 7,
        "review_days": 7, "safety_days": 2, "moq": 1, "pack_size": 1, **overrides})


def quote(**overrides):
    return SupplierQuote(**{"quote_id": "Q-A", "sku": "A", "supplier_id": "SUP",
        "observed_at": datetime(2026, 10, 15, 8, tzinfo=ZoneInfo("Asia/Karachi")), "currency": "USD",
        "unit_price": "4.00", "freight_unit_pkr": "150.00", "other_unit_pkr": "30.00", **overrides})


def rate(value="280", **overrides):
    return FXRate(**{"date": ORIGIN, "currency": "USD", "pkr_per_unit": value, "source": "synthetic", **overrides})


def test_architecture_financial_examples():
    p, q = product(), quote()
    base = replacement_cost(p, [q], [rate()], ORIGIN, Settings(), ScenarioAssumptions())
    assert base["replacement_cost_pkr"] == D("1300.00")
    changed = replacement_cost(p, [q], [rate("300")], ORIGIN, Settings(), ScenarioAssumptions())
    assert changed["replacement_cost_pkr"] == D("1380.00")
    pricing = price_recommendation(p, D("1380.00"), D("0.20"), PricingSettings())
    assert pricing["suggested_price_pkr"] == D("1725.00")
    assert pricing["historical_gross_difference_pkr"] == D("200.00")
    assert pricing["funding_shortfall_unit_pkr"] == D("180.00")
    assert pricing["current_replacement_margin"] < 0
    assert "BELOW_REPLACEMENT_COST" in pricing["warnings"]
    assert pricing["achievable_margin"] == D("0.20")


def test_fx_supplier_compounding_and_pkr_isolation():
    assumptions = ScenarioAssumptions(fx_change_pct="10", supplier_price_change_pct="10")
    result = replacement_cost(product(), [quote()], [rate()], ORIGIN, Settings(), assumptions)
    assert result["components"]["selected_pkr_per_currency_unit"] == D("308")
    assert result["replacement_cost_pkr"] == D("1535.20")
    pkr = quote(currency="PKR", unit_price="1000")
    base = replacement_cost(product(), [pkr], [], ORIGIN, Settings(), ScenarioAssumptions())
    fx_only = replacement_cost(product(), [pkr], [], ORIGIN, Settings(), ScenarioAssumptions(fx_change_pct="10"))
    assert base == fx_only and base["replacement_cost_pkr"] == D("1180.00")


def test_future_quotes_rates_supplier_and_deterministic_ties():
    first, last = quote(quote_id="Q-A", unit_price="3"), quote(quote_id="Q-Z")
    future = quote(quote_id="Q-future", observed_at="2026-10-16T08:00:00+05:00", unit_price="1")
    other_supplier = quote(quote_id="Q-other", supplier_id="OTHER", unit_price="1")
    future_rate = rate("999", date=date(2026, 10, 16))
    result = replacement_cost(product(), [last, other_supplier, future, first], [rate(), future_rate], ORIGIN, Settings(), ScenarioAssumptions())
    assert result["quote_id"] == "Q-Z" and result["replacement_cost_pkr"] == D("1300.00")
    assert replacement_cost(product(), [future], [rate()], ORIGIN, Settings(), ScenarioAssumptions())["warnings"] == ["MISSING_QUOTE"]
    assert "MISSING_FX_RATE" in replacement_cost(product(), [last], [future_rate], ORIGIN, Settings(), ScenarioAssumptions())["warnings"]


def test_expired_and_stale_indicative_not_fabricated_cost():
    old = quote(observed_at="2026-08-01T08:00:00+05:00", valid_until="2026-08-10")
    result = replacement_cost(product(), [old], [rate(date=date(2026, 10, 1))], ORIGIN, Settings(), ScenarioAssumptions())
    assert result["status"] == "indicative"
    assert set(result["warnings"]) == {"EXPIRED_QUOTE", "STALE_QUOTE", "STALE_FX_RATE"}
    missing = price_recommendation(product(), None, D("0.20"), PricingSettings())
    assert missing["status"] == "unavailable" and missing["suggested_price_pkr"] is None


@pytest.mark.parametrize("current,cost,step,cap,expected", [
    ("1000", "1380", "5", "10", "1100"),
    ("1001", "1380", "5", "0", "1001"),
    ("1000", "100", "5", "10", "900"),
    ("1003", "1380", "7", "1", "1008"),
    ("1003", "1380", "5", None, "1725"),
    ("1000", "0", "5", None, "5"),
])
def test_price_steps_and_caps(current, cost, step, cap, expected):
    result = price_recommendation(product(current_price_pkr=D(current)), D(cost), D("0.20"),
                                  PricingSettings(price_step_pkr=step, max_price_change_pct=cap))
    suggested = result["suggested_price_pkr"]
    assert suggested == D(expected)
    if cap is not None:
        assert abs(suggested - D(current)) <= D(current) * D(cap) / 100
    if "ROUNDING_CONSTRAINT" not in result["warnings"]:
        assert suggested % D(step) == 0
    assert ("TARGET_MARGIN_UNMET" in result["warnings"]) == (result["achievable_margin"] < D("0.20"))


@pytest.mark.parametrize("values", [
    {"fx_change_pct": "-100"}, {"supplier_price_change_pct": "-101"},
    {"additional_freight_pkr": "-1"}, {"extra_lead_days": -1}, {"target_margin_override": "20"},
    {"fx_change_pct": "NaN"}, {"supplier_price_change_pct": "Infinity"}, {"extra_lead_days": 1.5},
])
def test_invalid_scenario_assumptions(values):
    with pytest.raises(ValidationError):
        ScenarioAssumptions(**values)
