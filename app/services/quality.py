from zoneinfo import ZoneInfo
import pandas as pd
from app.core.config import TIMEZONE


def quality_summary(batch, settings):
    tables, origin = batch.tables, batch.end_date
    products, sales = tables["products.csv"], tables["daily_sales.csv"]
    warnings, cost_blockers = [], []
    missing_quotes, stale_quotes, expired_quotes = [], [], []
    missing_fx, stale_fx = [], []
    rates = {}
    for rate in tables["fx_rates.csv"]:
        if rate.date <= origin and (rate.currency not in rates or rate.date > rates[rate.currency].date):
            rates[rate.currency] = rate
    required_currencies = set()
    for product in products:
        quotes = [q for q in tables["supplier_quotes.csv"] if q.sku == product.sku
                  and q.observed_at.astimezone(ZoneInfo(TIMEZONE)).date() <= origin]
        if not quotes:
            missing_quotes.append(product.sku)
            continue
        quote = max(quotes, key=lambda q: (q.observed_at, q.quote_id))
        required_currencies.add(quote.currency)
        if (origin - quote.observed_at.astimezone(ZoneInfo(TIMEZONE)).date()).days > settings.quote_stale_days:
            stale_quotes.append(product.sku)
        if quote.valid_until and quote.valid_until < origin:
            expired_quotes.append(product.sku)
    for currency in sorted(required_currencies - {"PKR"}):
        if currency not in rates:
            missing_fx.append(currency)
        elif (origin - rates[currency].date).days > settings.fx_stale_days:
            stale_fx.append(currency)
    for code, values in (("missing_quotes", missing_quotes), ("missing_fx", missing_fx)):
        if values:
            cost_blockers.append({"code": code, "items": values})
    for code, values in (("stale_quotes", stale_quotes), ("expired_quotes", expired_quotes), ("stale_fx", stale_fx)):
        if values:
            warnings.append({"code": code, "items": values})
    frame = pd.DataFrame([s.model_dump() for s in sales])
    stockout = int((frame["store_open"] & ~frame["fully_in_stock"]).sum())
    closed = int((~frame["store_open"]).sum())
    if stockout:
        warnings.append({"code": "censored_sales", "message": f"{stockout} SKU-days may have stock-constrained sales"})
    orders = tables["purchase_orders.csv"]
    overdue = [p.po_id for p in orders if p.status == "open" and p.expected_arrival_date <= origin]
    if overdue:
        warnings.append({"code": "overdue_orders", "items": overdue})
    expected = len(products) * ((origin - batch.start_date).days + 1)
    inventory = tables["inventory.csv"]
    return {
        "schema_valid": True, "blocking_errors": [], "warnings": warnings,
        "product_count": len(products),
        "date_range": {"start": batch.start_date.isoformat(), "end": origin.isoformat()},
        "row_counts": {name.removesuffix(".csv"): len(rows) for name, rows in tables.items()},
        "sales_coverage": {"expected_sku_days": expected, "actual_sku_days": len(sales), "fraction": len(sales) / expected},
        "stockout_affected_sku_days": stockout, "closed_store_sku_days": closed,
        "closed_store_calendar_days": int(frame.loc[~frame["store_open"], "date"].nunique()),
        "quotations": {"missing": missing_quotes, "stale": stale_quotes, "expired": expired_quotes},
        "exchange_rates": {"missing": missing_fx, "stale": stale_fx},
        "purchase_orders": {status: sum(p.status == status for p in orders) for status in ("open", "received", "cancelled")},
        "inventory": {"on_hand_units": sum(i.on_hand_units for i in inventory),
                      "reserved_units": sum(i.reserved_units for i in inventory),
                      "available_units": sum(i.on_hand_units - i.reserved_units for i in inventory)},
        "readiness": {"current_cost_inputs_available": not cost_blockers, "cost_blockers": cost_blockers,
                      "model_training_assessed": False,
                      "note": "Schema validity does not establish future model sufficiency or recommendation readiness."},
        "freshness_policy": {"fx_days": settings.fx_stale_days, "quote_days": settings.quote_stale_days},
    }
