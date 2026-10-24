"""Origin-aware replacement costs. Round landed PKR once, then reuse that value."""
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from app.core.config import TIMEZONE


def money(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def replacement_cost(product, quotes, rates, origin, settings, assumptions):
    eligible = [q for q in quotes if q.sku == product.sku and q.supplier_id == product.supplier_id
                and q.observed_at.astimezone(ZoneInfo(TIMEZONE)).date() <= origin]
    result = {"status": "unavailable", "replacement_cost_pkr": None,
              "unrounded_replacement_cost_pkr": None, "quote_id": None, "observed_at": None,
              "currency": None, "rate_date": None, "rate_source": None, "components": {}, "warnings": []}
    if not eligible:
        result["warnings"].append("MISSING_QUOTE")
        return result
    quote = max(eligible, key=lambda q: (q.observed_at, q.quote_id))
    observed_date = quote.observed_at.astimezone(ZoneInfo(TIMEZONE)).date()
    result.update(quote_id=quote.quote_id, observed_at=quote.observed_at, currency=str(quote.currency))
    if (origin - observed_date).days > settings.quote_stale_days:
        result["warnings"].append("STALE_QUOTE")
    if quote.valid_until and quote.valid_until < origin:
        result["warnings"].append("EXPIRED_QUOTE")
    if quote.currency == "PKR":
        rate = Decimal(1)
        result["rate_source"] = "PKR identity; no FX observation required"
    else:
        eligible_rates = [r for r in rates if r.currency == quote.currency and r.date <= origin]
        if not eligible_rates:
            result["warnings"].append("MISSING_FX_RATE")
            return result
        selected = max(eligible_rates, key=lambda r: r.date)
        rate = selected.pkr_per_unit
        result.update(rate_date=selected.date, rate_source=selected.source)
        if (origin - selected.date).days > settings.fx_stale_days:
            result["warnings"].append("STALE_FX_RATE")
    selected_quote = quote.unit_price * (1 + assumptions.supplier_price_change_pct / 100)
    selected_rate = rate * (1 + assumptions.fx_change_pct / 100) if quote.currency != "PKR" else rate
    acquisition = selected_quote * selected_rate
    freight = quote.freight_unit_pkr + assumptions.additional_freight_pkr
    unrounded = acquisition + freight + quote.other_unit_pkr
    result.update(status="indicative" if result["warnings"] else "available",
        replacement_cost_pkr=money(unrounded), unrounded_replacement_cost_pkr=unrounded,
        components={"base_supplier_unit_price": quote.unit_price, "selected_supplier_unit_price": selected_quote,
                    "base_pkr_per_currency_unit": rate, "selected_pkr_per_currency_unit": selected_rate,
                    "acquisition_unit_pkr": acquisition, "base_freight_unit_pkr": quote.freight_unit_pkr,
                    "additional_freight_unit_pkr": assumptions.additional_freight_pkr,
                    "selected_freight_unit_pkr": freight, "other_unit_pkr": quote.other_unit_pkr,
                    "valid_until": quote.valid_until})
    return result
