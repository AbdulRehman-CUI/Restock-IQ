from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field
from app.data.contracts import PositiveMoney, Fraction
from app.services.costs import money

Percent = Annotated[Decimal, Field(ge=0, le=10000, max_digits=12, decimal_places=4, allow_inf_nan=False)]


class PricingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)
    price_step_pkr: PositiveMoney = Decimal("5.00")
    max_price_change_pct: Percent | None = None


def price_recommendation(product, cost, target_margin, settings: PricingSettings):
    current = product.current_price_pkr
    result = {"status": "unavailable", "current_price_pkr": current,
              "historical_unit_cost_pkr": product.historical_unit_cost_pkr,
              "historical_gross_difference_pkr": money(current - product.historical_unit_cost_pkr),
              "target_margin": target_margin, "current_replacement_margin": None,
              "funding_shortfall_unit_pkr": None, "unrounded_target_price_pkr": None,
              "suggested_price_pkr": None, "achievable_margin": None, "price_change_pkr": None,
              "absolute_price_change_pkr": None, "price_change_pct": None, "warnings": []}
    if cost is None:
        result["warnings"] = ["COST_UNAVAILABLE"]
        return result
    if not Decimal(0) <= target_margin <= Decimal("0.80"):
        raise ValueError("target_margin must be a fraction in [0, 0.80]")
    step = settings.price_step_pkr
    target = cost / (1 - target_margin)
    rounded = max(step, (target / step).to_integral_value(rounding=ROUND_CEILING) * step)
    suggested = rounded
    if settings.max_price_change_pct is not None:
        fraction = settings.max_price_change_pct / 100
        lower, upper = max(Decimal(0), current * (1 - fraction)), current * (1 + fraction)
        first = max(step, (lower / step).to_integral_value(rounding=ROUND_CEILING) * step)
        last = (upper / step).to_integral_value(rounding=ROUND_FLOOR) * step
        if first > last:
            suggested = current
            result["warnings"].append("ROUNDING_CONSTRAINT")
        else:
            suggested = max(first, min(rounded, last))
        if suggested != rounded:
            result["warnings"].append("PRICE_CHANGE_CAPPED")
    margin = (current - cost) / current
    achievable = (suggested - cost) / suggested
    if current < cost:
        result["warnings"].append("BELOW_REPLACEMENT_COST")
    if margin < target_margin:
        result["warnings"].append("BELOW_TARGET_MARGIN")
    if suggested < target:
        result["warnings"].append("TARGET_MARGIN_UNMET")
    change = suggested - current
    result.update(status="available", current_replacement_margin=margin,
        funding_shortfall_unit_pkr=money(max(Decimal(0), cost - current)),
        unrounded_target_price_pkr=target, suggested_price_pkr=money(suggested), achievable_margin=achievable,
        price_change_pkr=money(change), absolute_price_change_pkr=money(abs(change)), price_change_pct=change / current * 100)
    return result
