"""Versioned seller inputs and current pricing; never updates historical analyses."""
import hashlib
import json
from datetime import datetime, time, timezone
from decimal import Decimal, ROUND_CEILING, localcontext
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from app.api.schemas.market import QuoteInput, RateSettings
from app.core.errors import NotFound, StateConflict
from app.core.config import TIMEZONE
from app.db.market import MarketQuote, MarketSettings, PricingAssessment
from app.db.models import Dataset, Product, SupplierQuote, FXRate, InventorySnapshot
from app.services.costs import landed_cost, money
from app.services.fx import fx_status, instant, now_utc
from app.services.pricing import price_recommendation
from app.services.recommendations import wire

VERSION = "current-pricing-v1"
ASSUMPTIONS = [
    "Current FX-adjusted replacement estimate; no future FX, supplier-price prediction or live supplier monitoring.",
    "Supplier price and explicitly entered freight/other costs are held unchanged since their observation.",
    "Other acquisition costs are seller-entered nonrecoverable acquisition charges, including duties/taxes if applicable; none are calculated automatically.",
    "Minimum replacement-funding price excludes profit, operating expenses and any costs not entered; it is not full business break-even.",
    "Gross margin is (price minus cost) divided by price, not markup. Customer willingness to pay is not modeled.",
    "Inventory and selling-price context retain their dataset dates; forecasts and historical recommendations are not redated.",
]


def digest(value):
    return hashlib.sha256(json.dumps(wire(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def dataset_record(session, dataset_id, lock=False):
    stmt = select(Dataset).where(Dataset.id == str(dataset_id))
    row = session.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise NotFound("Dataset not found")
    return row


def settings_record(session, dataset_id):
    return session.scalar(select(MarketSettings).where(MarketSettings.dataset_id == str(dataset_id))
                          .order_by(MarketSettings.created_at.desc(), MarketSettings.id.desc()).limit(1))


def settings_view(row):
    return {"id": row.id if row else None, "created_at": row.created_at if row else None,
            "supplied_by": row.supplied_by if row else None,
            "values": row.values if row else RateSettings().model_dump(mode="json")}


def get_settings(db, dataset_id):
    with db.sessions() as session:
        dataset_record(session, dataset_id)
        return settings_view(settings_record(session, dataset_id))


def save_settings(db, dataset_id, values, supplied_by, *, can_configure_schedule=True):
    with db.transaction() as session:
        dataset = dataset_record(session, dataset_id, lock=True)
        if values.payment_fee_sku and session.scalar(select(Product.sku).where(
                Product.dataset_id == str(dataset_id), Product.sku == values.payment_fee_sku)) is None:
            raise StateConflict("Payment-fee batch SKU must belong to this dataset")
        if dataset.provenance == "synthetic" and values.mode == "manual_bank" and not values.real_fx_opt_in:
            raise StateConflict("Explicitly opt in to real FX before using a bank rate with synthetic products")
        prior = settings_record(session, dataset_id)
        payload = values.model_dump(mode="json")
        if not can_configure_schedule:
            scheduled = prior.values.get("auto_assess", False) if prior else False
            if "auto_assess" in values.model_fields_set and values.auto_assess != scheduled:
                raise StateConflict("Scheduled reassessment can only be configured locally")
            payload["auto_assess"] = scheduled
        if prior and prior.values == payload:
            return settings_view(prior)
        row = MarketSettings(id=str(uuid4()), dataset_id=str(dataset_id), created_at=instant(now_utc()),
                             supplied_by=supplied_by, values=payload)
        session.add(row)
        session.flush()
        return settings_view(row)


def quote_view(row):
    return {key: getattr(row, key) for key in ("id", "dataset_id", "sku", "supplier_id", "observed_at", "created_at",
                                               "supplied_by", "reconfirms_id", "values")}


def validate_quote_product(session, dataset_id, quote):
    product = session.scalar(select(Product).where(Product.dataset_id == str(dataset_id), Product.sku == quote.sku))
    if product is None or product.supplier_id != quote.supplier_id:
        raise StateConflict(f"SKU {quote.sku} must use its dataset's existing primary supplier")
    if quote.unit_basis == "pack" and quote.pack_units != product.pack_size:
        raise StateConflict(f"SKU {quote.sku}: quoted pack must match product pack_size={product.pack_size}")
    return product


def insert_quote(session, dataset_id, quote, supplied_by, identifier=None, reconfirms_id=None):
    validate_quote_product(session, dataset_id, quote)
    identifier = identifier or str(uuid4())
    values = quote.model_dump(mode="json")
    existing = session.get(MarketQuote, identifier)
    if existing:
        if existing.dataset_id != str(dataset_id) or existing.values != values or existing.reconfirms_id != reconfirms_id:
            raise StateConflict("Idempotency key already belongs to a different quote")
        return quote_view(existing)
    row = MarketQuote(id=identifier, dataset_id=str(dataset_id), sku=quote.sku, supplier_id=quote.supplier_id,
        observed_at=instant(quote.observed_at), created_at=instant(now_utc()), values=values,
        supplied_by=supplied_by, reconfirms_id=reconfirms_id)
    session.add(row)
    session.flush()
    return quote_view(row)


def create_quote(db, dataset_id, quote, supplied_by, identifier=None):
    with db.transaction() as session:
        dataset_record(session, dataset_id, lock=True)
        return insert_quote(session, dataset_id, quote, supplied_by, identifier)


def reconfirm_quote(db, dataset_id, quote_id, body, supplied_by, identifier=None):
    with db.transaction() as session:
        dataset_record(session, dataset_id, lock=True)
        old = session.get(MarketQuote, str(quote_id))
        if not old or old.dataset_id != str(dataset_id):
            raise NotFound("Quote version not found")
        if body.observed_at < datetime.fromisoformat(old.observed_at):
            raise StateConflict("Reconfirmation cannot precede the original observation")
        values = dict(old.values, observed_at=body.observed_at, valid_until=body.valid_until,
                      note=body.note, source_type="manual")
        return insert_quote(session, dataset_id, QuoteInput.model_validate(values), supplied_by, identifier, old.id)


def quote_history(db, dataset_id, sku=None, limit=50, offset=0):
    with db.sessions() as session:
        dataset_record(session, dataset_id)
        stmt = select(MarketQuote).where(MarketQuote.dataset_id == str(dataset_id))
        if sku:
            stmt = stmt.where(MarketQuote.sku == sku)
        return [quote_view(row) for row in session.scalars(stmt.order_by(MarketQuote.observed_at.desc(),
            MarketQuote.created_at.desc()).limit(limit).offset(offset))]


def selected_quote(session, dataset_id, product, now, order_quantity=None):
    row = session.scalar(select(MarketQuote).where(MarketQuote.dataset_id == str(dataset_id),
        MarketQuote.sku == product.sku, MarketQuote.observed_at <= instant(now)).order_by(
            MarketQuote.observed_at.desc(), MarketQuote.created_at.desc(), MarketQuote.id.desc()).limit(1))
    # An explicit current quote supersedes dataset quotes, including a backdated correction.
    if row:
        if order_quantity is not None:
            tiers = list(session.scalars(select(MarketQuote).where(MarketQuote.dataset_id == str(dataset_id),
                MarketQuote.sku == product.sku, MarketQuote.observed_at == row.observed_at)
                .order_by(MarketQuote.created_at.desc()).limit(100)))
            applicable = [q for q in tiers if q.values["min_quantity"] <= order_quantity and
                          (q.values["max_quantity"] is None or order_quantity <= q.values["max_quantity"])]
            if applicable:
                row = max(applicable, key=lambda q: (q.values["min_quantity"], q.created_at, q.id))
        return {**quote_view(row), "confirmed": True}
    originals = list(session.scalars(select(SupplierQuote).where(SupplierQuote.dataset_id == str(dataset_id),
                                                                SupplierQuote.sku == product.sku)))
    eligible = [q for q in originals if datetime.fromisoformat(q.observed_at) <= now]
    if not eligible:
        return None
    old = max(eligible, key=lambda q: (datetime.fromisoformat(q.observed_at), q.quote_id))
    expiry = datetime.combine(old.valid_until, time.max, ZoneInfo(TIMEZONE)) if old.valid_until else None
    return {"id": "dataset:" + old.quote_id, "confirmed": False, "supplied_by": "dataset import",
            "observed_at": instant(datetime.fromisoformat(old.observed_at)), "values": wire({
                "sku": old.sku, "supplier_id": old.supplier_id, "currency": old.currency,
                "unit_price": old.unit_price, "freight_unit_pkr": old.freight_unit_pkr, "other_unit_pkr": old.other_unit_pkr,
                "observed_at": datetime.fromisoformat(old.observed_at), "valid_until": expiry,
                "unit_basis": "unit", "pack_units": 1, "min_quantity": 1, "max_quantity": None,
                "source_type": "CSV", "source_reference": "", "note": "Historical dataset quote; not currently reconfirmed"})}


def product_result(product, quote, rate_config, rate, fx, settings, now):
    warnings, assumptions = [], []
    result = {"sku": product.sku, "name": product.name, "status": "unavailable", "quote": quote,
        "quote_age_days": None, "quote_validity": "unknown", "quote_price_assumed_unchanged": True,
        "effective_rate": rate, "components": {}, "replacement_cost_pkr": None,
        "unrounded_replacement_cost_pkr": None, "minimum_replacement_funding_price_pkr": None,
        "unrounded_funding_shortfall_unit_pkr": None,
        "pricing": price_recommendation(product, None, rate_config.target_margin if rate_config.target_margin is not None
            else product.target_margin, rate_config.pricing), "warnings": warnings, "assumptions": assumptions,
        "funding_assurance": False}
    if quote is None:
        warnings.append("MISSING_QUOTE")
        return result
    q = quote["values"]
    age = max(0, (now - datetime.fromisoformat(quote["observed_at"])).days)
    expired = bool(q["valid_until"] and datetime.fromisoformat(q["valid_until"]) <= now)
    result.update(quote_age_days=age, quote_validity="expired" if expired else "dated" if q["valid_until"] else "unknown")
    if expired:
        warnings.append("EXPIRED_QUOTE")
    if age > settings.quote_stale_days:
        warnings.append("STALE_QUOTE")
    if not quote["confirmed"]:
        warnings.append("UNCONFIRMED_QUOTE")
    if not q["valid_until"]:
        warnings.append("QUOTE_VALIDITY_UNKNOWN")
    assumptions.append(f"Supplier price assumed unchanged since {quote['observed_at']}; costs are seller-provided.")
    quantity = rate_config.order_quantity
    if ((q["min_quantity"] > 1 or q["max_quantity"] is not None) and quantity is None) or (
            quantity is not None and (quantity < q["min_quantity"] or q["max_quantity"] is not None and quantity > q["max_quantity"])):
        warnings.append("QUOTE_QUANTITY_MISMATCH")
    if q["unit_basis"] == "pack" and q["pack_units"] != product.pack_size:
        warnings.append("QUOTE_UNIT_MISMATCH")
    if quantity and (quantity < product.moq or quantity % product.pack_size):
        warnings.append("ORDER_QUANTITY_MISMATCH")
    if q["freight_unit_pkr"] is None or q["other_unit_pkr"] is None:
        warnings.append("MISSING_ACQUISITION_COSTS")
    if q["currency"] == "PKR":
        rate = Decimal(1)
    else:
        if rate is None:
            warnings.append("MISSING_FX_RATE")
        if rate_config.mode == "reference_spread":
            if fx["status"] == "stale":
                warnings.append("STALE_FX_RATE")
            if fx["refresh_failed"]:
                warnings.append("FX_REFRESH_FAILED")
        else:
            manual_age = (now - rate_config.manual_observed_at).total_seconds()
            if manual_age > settings.fx_stale_days * 86400:
                warnings.append("STALE_FX_RATE")
            if rate_config.manual_valid_until and rate_config.manual_valid_until <= now:
                warnings.append("MANUAL_RATE_EXPIRED")
                rate = None
            elif not rate_config.manual_valid_until:
                warnings.append("MANUAL_RATE_VALIDITY_UNKNOWN")
    result["effective_rate"] = rate
    blockers = {"MISSING_ACQUISITION_COSTS", "MISSING_FX_RATE", "MANUAL_RATE_EXPIRED", "QUOTE_QUANTITY_MISMATCH",
                "QUOTE_UNIT_MISMATCH", "ORDER_QUANTITY_MISMATCH"}
    if blockers.intersection(warnings):
        return result
    with localcontext() as context:
        context.prec = 38
        supplier = Decimal(q["unit_price"]) / q["pack_units"]
        fee_total = rate_config.payment_fee_total_pkr if rate_config.payment_fee_sku == product.sku else Decimal(0)
        fee = fee_total / quantity if fee_total else Decimal(0)
        cost = landed_cost(supplier, rate, Decimal(q["freight_unit_pkr"]), Decimal(q["other_unit_pkr"]), fee)
        pricing = price_recommendation(product, cost, rate_config.target_margin if rate_config.target_margin is not None
                                       else product.target_margin, rate_config.pricing)
        warnings.extend(pricing["warnings"])
        if pricing["suggested_price_pkr"] < cost:
            warnings.append("PRICE_CAP_PREVENTS_REPLACEMENT")
        result.update(components={"supplier_price_per_inventory_unit": supplier,
            "supplier_acquisition_pkr": supplier * rate, "effective_pkr_per_currency_unit": rate,
            "freight_unit_pkr": Decimal(q["freight_unit_pkr"]), "other_unit_pkr": Decimal(q["other_unit_pkr"]),
            "allocated_payment_fee_unit_pkr": fee, "payment_fee_total_pkr": fee_total,
            "batch_quantity": quantity, "fee_allocation": "equal per inventory unit" if fee else "no fixed fee"},
            replacement_cost_pkr=money(cost), unrounded_replacement_cost_pkr=cost,
            unrounded_funding_shortfall_unit_pkr=max(Decimal(0), cost - product.current_price_pkr),
            minimum_replacement_funding_price_pkr=cost.quantize(Decimal("0.01"), rounding=ROUND_CEILING), pricing=pricing,
            status="indicative" if warnings else "available")
    # No guaranteed future purchasing power, even for complete current inputs.
    assumptions.append("A recommendation funds estimated replacement only under these inputs; future prices may change.")
    return result


def create_assessment(db, settings, dataset_id, sku=None, now=None):
    now = now or now_utc()
    fx = fx_status(db, settings, now)
    with db.transaction() as session:
        dataset = dataset_record(session, dataset_id, lock=True)
        saved = settings_record(session, dataset_id)
        config = RateSettings.model_validate(saved.values if saved else {})
        products = list(session.scalars(select(Product).where(Product.dataset_id == str(dataset_id)).order_by(Product.sku)))
        if sku:
            products = [p for p in products if p.sku == sku]
            if not products:
                raise NotFound("Product not found")
        synthetic_fx = dataset.provenance == "synthetic" and not config.real_fx_opt_in
        rate = None
        if synthetic_fx:
            original = session.scalar(select(FXRate).where(FXRate.dataset_id == str(dataset_id), FXRate.currency == "USD",
                FXRate.date <= dataset.end_date).order_by(FXRate.date.desc()).limit(1))
            if original:
                rate = original.pkr_per_unit * (1 + config.spread_percent / 100)
            fx = dict(fx, status="stale" if rate else "unavailable", observation=None, age_seconds=None,
                      refresh_failed=False, error=None,
                      notice=f"Synthetic dataset FX as of {original.date if original else 'unavailable'}; no real FX applied.")
        elif config.mode == "manual_bank":
            rate = None if config.manual_valid_until and config.manual_valid_until <= now else config.manual_rate
        elif fx["observation"]:
            rate = Decimal(fx["observation"]["rate"]) * (1 + config.spread_percent / 100)
        selection = {"mode": "synthetic_reference_spread" if synthetic_fx else config.mode,
            "effective_usd_pkr_rate": rate,
            "spread_percent": config.spread_percent if config.mode == "reference_spread" else None,
            "fx_observation_id": fx["observation"]["id"] if not synthetic_fx and config.mode == "reference_spread" and fx["observation"] else None,
            "source": "synthetic dataset" if synthetic_fx else config.manual_source if config.mode == "manual_bank" else "ExchangeRate-API daily reference",
            "observed_at": original.date.isoformat() if synthetic_fx and original else config.manual_observed_at if config.mode == "manual_bank" else
                           fx["observation"]["observed_at"] if fx["observation"] else None,
            "manual_supplied_by": saved.supplied_by if saved and config.mode == "manual_bank" else None,
            "valid_until": config.manual_valid_until if config.mode == "manual_bank" else None}
        results = [product_result(p, selected_quote(session, dataset_id, p, now, config.order_quantity), config, rate, fx, settings, now) for p in products]
        inventories = list(session.scalars(select(InventorySnapshot.snapshot_date).where(InventorySnapshot.dataset_id == str(dataset_id)).distinct()))
        label = ("Synthetic products with real FX" if config.real_fx_opt_in else "Entirely synthetic pricing") if dataset.provenance == "synthetic" else "Current replacement-cost estimate"
        context = {"dataset_origin": dataset.end_date, "inventory_snapshot_dates": inventories,
                   "selling_price_as_of": dataset.end_date, "demand_forecast": "Not refreshed or used by current pricing",
                   "provenance": dataset.provenance}
        payload = wire({"dataset_id": str(dataset_id), "assessed_at": instant(now), "calculation_version": VERSION,
            "label": label, "context": context, "settings": settings_view(saved), "fx": fx, "rate_selection": selection,
            "products": results, "assumptions": ASSUMPTIONS, "comparison": None})
        # Exclude retrieval clock: identical inputs, freshness states and calculation results reuse a snapshot.
        uses_reference = not synthetic_fx and config.mode == "reference_spread" and any(
            p["quote"] and p["quote"]["values"]["currency"] == "USD" for p in results)
        relevant_fx = {key: fx[key] for key in ("status", "observation", "refresh_failed", "error", "notice")} if uses_reference else None
        relevant_products = [{key: value for key, value in p.items() if key != "quote_age_days"} for p in results]
        fingerprint = digest({"version": VERSION, "label": label, "settings": payload["settings"],
                              "products": relevant_products, "context": context, "fx": relevant_fx})
        prior = session.scalar(select(PricingAssessment).where(PricingAssessment.dataset_id == str(dataset_id),
                                                               PricingAssessment.fingerprint == fingerprint))
        if prior:
            return prior.result
        payload.update(id=str(uuid4()), fingerprint=fingerprint)
        recent = list(session.scalars(select(PricingAssessment).where(PricingAssessment.dataset_id == str(dataset_id))
                                     .order_by(PricingAssessment.created_at.desc()).limit(20)))
        previous = next((r.result for r in recent if [p["sku"] for p in r.result["products"]] ==
                         [p["sku"] for p in payload["products"]]), None)
        if previous:
            payload["comparison"] = compare_payloads(previous, payload)
            for product, change in zip(payload["products"], payload["comparison"]["products"]):
                if change["alerts"]:
                    product["warnings"].extend(change["alerts"])
                    product["assumptions"].append(change["explanation"])
                    if product["status"] == "available":
                        product["status"] = "indicative"
        session.add(PricingAssessment(id=payload["id"], dataset_id=str(dataset_id), created_at=instant(now),
                                     fingerprint=fingerprint, result=payload))
        return payload


def get_assessment(db, identifier):
    with db.sessions() as session:
        row = session.get(PricingAssessment, str(identifier))
        if not row:
            raise NotFound("Current pricing assessment not found")
        return row.result


def compare_assessments(db, before_id, after_id):
    before, after = get_assessment(db, before_id), get_assessment(db, after_id)
    return compare_payloads(before, after)


def compare_payloads(before, after):
    if before["dataset_id"] != after["dataset_id"]:
        raise StateConflict("Compare assessments belonging to the same dataset")
    old = {p["sku"]: p for p in before["products"]}
    changes = []
    for p in after["products"]:
        a = old.get(p["sku"])
        if not a:
            continue
        changed = []
        if a["quote"] != p["quote"]:
            changed.append("supplier quote")
        if before["settings"]["values"] != after["settings"]["values"]:
            changed.append("rate/cost/margin settings")
        if a["effective_rate"] != p["effective_rate"]:
            changed.append("FX")
        fx_only = changed == ["FX"] and bool(p["quote"]) and p["quote"]["values"]["currency"] == "USD"
        left, right = a["pricing"]["suggested_price_pkr"], p["pricing"]["suggested_price_pkr"]
        delta = Decimal(right) - Decimal(left) if left is not None and right is not None else None
        material = bool(fx_only and delta is not None and left and abs(delta) / Decimal(left) >= Decimal("0.01"))
        changes.append(wire({"sku": p["sku"], "changed_inputs": changed, "attribution": "FX only" if fx_only else "combined inputs" if len(changed) > 1 else ", ".join(changed) or "unchanged financial inputs",
            "suggested_price_change_pkr": delta, "alerts": ["MATERIAL_FX_PRICE_CHANGE"] if material else [],
            "explanation": f"FX changed; the supplier’s USD price is assumed unchanged since {p['quote']['observed_at']}." if fx_only else "Review the listed quote and setting changes separately."}))
    return {"before_id": before["id"], "after_id": after["id"], "products": changes}


def auto_assess(db, settings):
    with db.sessions() as session:
        dataset_ids = list(session.scalars(select(MarketSettings.dataset_id).distinct()))
    results = []
    for identifier in dataset_ids:
        config = get_settings(db, identifier)["values"]
        if config.get("auto_assess"):
            results.append(create_assessment(db, settings, identifier)["id"])
    return results
