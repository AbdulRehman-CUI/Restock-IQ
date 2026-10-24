"""Chronological historical simulation, unrelated to future recommendation services."""
import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from math import ceil
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import TIMEZONE
from app.core.enums import OrderStatus
from app.data.contracts import (CONTRACTS, DailySale, FXRate, Inventory,
                                Product, PurchaseOrder, SupplierQuote)
from app.services.ingestion import make_manifest, write_manifest
from app.services.quality import quality_summary
from app.services.validation import read_batch


class GeneratorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed: int = Field(default=42, ge=0, le=2**32 - 1)
    start_date: date = date(2025, 10, 16)
    days: int = Field(default=365, ge=1, le=3650)
    sku_count: int = Field(default=20, ge=1, le=200)


@dataclass
class GeneratedDataset:
    dataset_id: str
    operational_dir: Path
    evaluation_dir: Path
    manifest: dict


def money(value):
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def write_records(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            if hasattr(row, "model_dump"):
                row = row.model_dump(mode="python")
            writer.writerow({k: "" if v is None else str(v).lower() if isinstance(v, bool)
                             else v.isoformat() if isinstance(v, (date, datetime)) else str(v)
                             for k, v in row.items()})


def simulate(config: GeneratorConfig):
    dates = [config.start_date + timedelta(days=t) for t in range(config.days)]
    origin = dates[-1]
    seeds = np.random.SeedSequence(config.seed).spawn(config.sku_count + 1)
    fx_rng = np.random.default_rng(seeds[0])
    fx_history = []
    rate = 278.0
    for t, day in enumerate(dates):
        shock = float(fx_rng.uniform(-0.025, 0.045)) if t and fx_rng.random() < 0.022 else 0
        rate = max(180, rate * (1 + 0.00008 + fx_rng.normal(0, 0.0015) + shock))
        fx_history.append(Decimal(str(rate)).quantize(Decimal("0.000001")))
    tables = {filename: [] for filename in CONTRACTS}
    for day, rate in zip(dates, fx_history):
        for currency, value in (("PKR", Decimal("1.000000")), ("USD", rate)):
            tables["fx_rates.csv"].append(FXRate(date=day, currency=currency, pkr_per_unit=value, source="synthetic"))
    reconciliation, order_truth = [], []
    categories = [("CHG", "chargers", "USB-C charger", 4.1, 9),
                  ("CBL", "cables", "Braided cable", 0.85, 21),
                  ("HDP", "headphones", "Wireless headphones", 9.4, 5),
                  ("PWB", "power banks", "Power bank", 7.2, 7),
                  ("LST", "laptop stands", "Laptop stand", 5.4, 3)]
    for index in range(config.sku_count):
        rng = np.random.default_rng(seeds[index + 1])
        prefix, category, name, usd_base, base_demand = categories[index % 5]
        sku = f"{prefix}-{index + 1:03d}"
        supplier = f"SUP-{index % 7 + 1:02d}"
        currency = "USD" if index % 3 == 0 else "PKR"
        variation = float(rng.uniform(0.7, 1.5))
        baseline = base_demand * variation
        base_quote = usd_base * variation * (1 if currency == "USD" else float(fx_history[0]))
        freight, other = money(40 + index * 4), money(8 + index % 4 * 6)
        initial_cost = money(money(base_quote) * (fx_history[0] if currency == "USD" else 1) + freight + other)
        lead = 5 + index % 12
        product = Product(sku=sku, name=f"{name} {index + 1} (synthetic)", category=category,
                          supplier_id=supplier, current_price_pkr=money(initial_cost / Decimal("0.72")),
                          historical_unit_cost_pkr=initial_cost, target_margin=Decimal("0.20") + Decimal(index % 4) / 100,
                          default_lead_days=lead, safety_days=0 if index == 0 else 2 + index % 3,
                          review_days=7 + index % 3, moq=6 + (index % 4) * 6, pack_size=2 + index % 3)
        tables["products.csv"].append(product)
        stock = ceil(baseline * (2 if index < 2 else lead + 8))
        pending = []
        uncensored_history = []
        quote = None
        quote_drift = 1.0
        quote_counter = 0
        trend = float(rng.uniform(-0.13, 0.24))
        lag = 8 + index % 19
        pass_through = 0 if index % 4 == 1 else float(rng.uniform(0.35, 0.8))
        event_offset = int(rng.integers(0, max(1, config.days)))
        for t, day in enumerate(dates):
            # Quote observations occur at 08:00, before the day's trading and orders.
            if t % 14 == 0:
                quote_counter += 1
                quote_drift *= 1 + float(rng.normal(0.001, 0.018))
                if currency == "USD":
                    price = money(base_quote * quote_drift)
                else:
                    delayed_fx_ratio = float(fx_history[max(0, t - lag)] / fx_history[0])
                    price = money(base_quote * quote_drift * (1 + pass_through * (delayed_fx_ratio - 1)))
                quote = SupplierQuote(quote_id=f"Q-{sku}-{quote_counter:04d}", sku=sku, supplier_id=supplier,
                                      observed_at=datetime.combine(day, time(8), ZoneInfo(TIMEZONE)),
                                      currency=currency, unit_price=max(Decimal("0.01"), price),
                                      freight_unit_pkr=freight, other_unit_pkr=other,
                                      valid_until=day + timedelta(days=28))
                tables["supplier_quotes.csv"].append(quote)
            opening = stock
            receipts, receipt_ids = 0, []
            for entry in pending:
                po = entry["record"]
                if po.status == "open" and entry["planned_actual"] == day:
                    stock += po.ordered_units
                    receipts += po.ordered_units
                    receipt_ids.append(po.po_id)
                    po.status = OrderStatus.RECEIVED
                    po.actual_arrival_date = day
            available = stock
            store_open = t % 61 != 30
            promo = (t + index * 3) % 47 < 4
            event = 0 <= t - event_offset < 4
            weekday = (0.90, 0.92, 0.96, 1.0, 1.12, 1.32, 1.18)[day.weekday()]
            shift = 1.30 if index % 8 == 3 and t >= int(config.days * 0.75) else 1
            mean_demand = baseline * weekday * (1 + trend * t / max(1, config.days - 1)) * shift
            mean_demand *= (1.65 if promo else 1) * (1.7 if event else 1)
            # Gamma-Poisson mixture gives overdispersed integer customer demand.
            demand = int(rng.poisson(rng.gamma(12, mean_demand / 12)))
            sold = min(stock, demand) if store_open else 0
            stock -= sold
            fully_in_stock = store_open and available > sold
            landed_now = quote.unit_price * (fx_history[t] if currency == "USD" else 1) + freight + other
            regular_price = money(landed_now / Decimal("0.72"))
            selling_price = money(regular_price * (Decimal("0.90") if promo else 1))
            tables["daily_sales.csv"].append(DailySale(date=day, sku=sku, units_sold=sold,
                selling_price_pkr=selling_price, promo_active=promo, fully_in_stock=fully_in_stock, store_open=store_open))
            if fully_in_stock:
                uncensored_history.append(sold)
            # This estimate sees only fulfilled, uncensored observations up to today.
            # Baseline is an explicit prior stocking assumption until observations exist.
            estimate = float(np.mean(uncensored_history[-28:])) if uncensored_history else baseline
            position = stock + sum(e["record"].ordered_units for e in pending if e["record"].status == "open")
            reorder_level = ceil(estimate * (lead + product.safety_days))
            ordered_id = ""
            if position <= reorder_level:
                target = ceil(estimate * (lead + product.review_days + product.safety_days))
                quantity = ceil(max(product.moq, target - position) / product.pack_size) * product.pack_size
                po_id = f"PO-{sku}-{len(pending) + 1:04d}"
                delayed = rng.random() < (0.65 if index == 0 else 0.23)
                delivery_days = max(1, lead + int(rng.integers(3, 11) if delayed else rng.integers(-2, 3)))
                planned_actual = day + timedelta(days=delivery_days)
                cancelled = bool(rng.random() < 0.035)
                po = PurchaseOrder(po_id=po_id, sku=sku, supplier_id=supplier, ordered_at=day,
                                   ordered_units=quantity, expected_arrival_date=day + timedelta(days=lead),
                                   unit_price=quote.unit_price, currency=currency,
                                   status="cancelled" if cancelled else "open")
                pending.append({"record": po, "planned_actual": planned_actual})
                tables["purchase_orders.csv"].append(po)
                order_truth.append({"po_id": po_id, "sku": sku, "planned_actual_arrival_date": planned_actual,
                                    "cancelled": cancelled, "synthetic": True})
                ordered_id = po_id
            reconciliation.append({"date": day, "sku": sku, "opening_units": opening,
                "receipt_units": receipts, "receipt_po_ids": "|".join(receipt_ids), "available_units": available,
                "latent_demand_units": demand, "units_sold": sold, "closing_units": stock,
                "store_open": store_open, "fully_in_stock": fully_in_stock,
                "fictional_demand_event": event, "ordered_po_id": ordered_id,
                "policy_demand_estimate": f"{estimate:.6f}", "synthetic": True})
        product.current_price_pkr = selling_price
        tables["inventory.csv"].append(Inventory(sku=sku, snapshot_date=origin, on_hand_units=stock, reserved_units=0))
    return tables, reconciliation, order_truth


def generate_demo(settings, config: GeneratorConfig, *, progress=None):
    dataset_id = str(uuid4())
    operational = settings.data_dir / "demo" / dataset_id
    evaluation = settings.data_dir / "evaluation_only" / dataset_id
    # Exclusive new UUID directories; never write in uploads or replace an old demo.
    operational.mkdir(parents=True, exist_ok=False)
    evaluation.mkdir(parents=True, exist_ok=False)
    tables, reconciliation, order_truth = simulate(config)
    if progress:
        progress(55, "exporting")
    for filename, contract in CONTRACTS.items():
        write_records(operational / filename, list(contract.model_fields), tables[filename])
    write_records(evaluation / "simulation.csv", list(reconciliation[0]), reconciliation)
    truth_fields = ["po_id", "sku", "planned_actual_arrival_date", "cancelled", "synthetic"]
    write_records(evaluation / "order_truth.csv", truth_fields, order_truth)
    batch = read_batch(operational)
    quality = quality_summary(batch, settings)
    manifest = make_manifest(batch, quality, dataset_id, "synthetic", config.model_dump(mode="json"))
    write_manifest(operational / "manifest.json", manifest)
    (evaluation / "README.txt").write_text(
        "SYNTHETIC EVALUATION ONLY. Hidden customer demand and delivery plans are simulation truth.\n"
        "Never import these files, expose through operational endpoints, or use as model features/targets.\n"
        "opening + receipts - observed sales = closing; receipts occur before trading.\n"
        "Closed-day latent demand is hypothetical footfall; observed sales remain zero.\n", encoding="utf-8")
    return GeneratedDataset(dataset_id, operational, evaluation, manifest)
