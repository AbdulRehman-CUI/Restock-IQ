"""Recursive rolling-origin evaluation, with paired and complete-window coverage."""
from datetime import timedelta
import numpy as np

from app.core.errors import StateConflict
from app.ml.predict import predict_recursive

HORIZONS = (1, 7, 30)
ORIGIN_STRIDE = 7
CUMULATIVE_MIN_WINDOWS = 3
SELECTION_RULE = {"primary_horizon_days": 7, "score": "paired_uncensored_sku_day_MAE",
                  "minimum_paired_observations": 30, "minimum_origins": 3,
                  "tie": "baseline", "tie_tolerance": 1e-9}


def split_dates(dates):
    dates = sorted(set(dates))
    train_end, validation_end = int(len(dates) * 0.70), int(len(dates) * 0.85)
    if train_end < 1 or validation_end <= train_end or validation_end >= len(dates):
        raise StateConflict("At least seven consecutive dates are needed for nonempty train/validation/test partitions")
    return {"train": (dates[0], dates[train_end - 1]),
            "validation": (dates[train_end], dates[validation_end - 1]),
            "test": (dates[validation_end], dates[-1])}


def scores(actual, predicted, minimum_count=1):
    if len(actual) < minimum_count:
        return {"status": "insufficient_coverage", "count": len(actual), "mae_units": None,
                "wape": None, "wape_status": "no_observations" if not actual else "insufficient_coverage",
                "actual_units": float(sum(actual))}
    actual, predicted = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    errors = np.abs(predicted - actual)
    denominator = float(actual.sum())
    return {"status": "ok", "count": len(actual), "mae_units": float(errors.mean()),
            "wape": float(errors.sum() / denominator) if denominator else None,
            "wape_status": "defined" if denominator else "undefined_zero_actual_units", "actual_units": denominator}


def summarize_pairs(pairs, minimum_count=1):
    return {method: scores([p[0] for p in pairs], [p[index] for p in pairs], minimum_count)
            for method, index in (("baseline", 1), ("random_forest", 2))}


def evaluate_partition(sales, products, candidate, start, end):
    targets = {(r.sku, r.date): r for r in sales.itertuples(index=False)}
    results = {}
    for horizon in HORIZONS:
        origin = start - timedelta(days=1)
        origins = []
        while origin + timedelta(days=horizon) <= end:
            origins.append(origin)
            origin += timedelta(days=ORIGIN_STRIDE)
        pairs, complete_windows = [], []
        per_sku = {sku: [] for sku in sorted(products.sku)}
        per_windows = {sku: [] for sku in sorted(products.sku)}
        counts = {"candidate_sku_days": 0, "closed_targets": 0, "censored_open_targets": 0,
                  "unpaired_usable_targets": 0, "paired_targets": 0,
                  "candidate_windows": len(origins) * len(products), "excluded_incomplete_windows": 0}
        paired_origins = set()
        fallback_reasons = {}
        for origin in origins:
            forest_rows, statuses = predict_recursive(sales, products, origin, horizon, candidate, "random_forest")
            baseline_rows, _ = predict_recursive(sales, products, origin, horizon, candidate, "baseline")
            forest = {(r["sku"], r["date"]): r for r in forest_rows}
            base = {(r["sku"], r["date"]): r for r in baseline_rows}
            for status in statuses:
                if status["fallback_applied"] or status["status"] != "forecasted":
                    for reason in status["reason_codes"]:
                        fallback_reasons[reason] = fallback_reasons.get(reason, 0) + 1
            for sku in sorted(products.sku):
                window = []
                for step in range(1, horizon + 1):
                    key = sku, origin + timedelta(days=step)
                    actual, rf, baseline = targets.get(key), forest.get(key), base.get(key)
                    counts["candidate_sku_days"] += 1
                    if actual is None or not actual.store_open:
                        counts["closed_targets"] += 1
                    elif not actual.fully_in_stock:
                        counts["censored_open_targets"] += 1
                    elif not rf or rf["method"] != "random_forest" or not baseline:
                        counts["unpaired_usable_targets"] += 1
                    else:
                        pair = (float(actual.units_sold), baseline["expected_units"], rf["expected_units"])
                        pairs.append(pair)
                        per_sku[sku].append(pair)
                        window.append(pair)
                        paired_origins.add(origin)
                        counts["paired_targets"] += 1
                if len(window) == horizon:
                    sums = tuple(sum(p[i] for p in window) for i in range(3))
                    complete_windows.append(sums)
                    per_windows[sku].append(sums)
                else:
                    counts["excluded_incomplete_windows"] += 1
        counts["paired_origin_count"] = len(paired_origins)
        counts["complete_window_count"] = len(complete_windows)
        results[str(horizon)] = {"origins": [d.isoformat() for d in origins],
            "counts": counts, "methods": summarize_pairs(pairs),
            "per_product": {sku: summarize_pairs(values) for sku, values in per_sku.items()},
            "cumulative_demand": {"definition": "MAE of horizon-summed demand; only fully uncensored/open paired windows",
                                  "minimum_complete_windows": CUMULATIVE_MIN_WINDOWS,
                                  "methods": summarize_pairs(complete_windows, CUMULATIVE_MIN_WINDOWS),
                                  "per_product": {sku: summarize_pairs(values, CUMULATIVE_MIN_WINDOWS) for sku, values in per_windows.items()}},
            "fallback_reason_counts_by_sku_origin": fallback_reasons}
    return results


def select_method(validation):
    result = validation["7"]
    base, forest = result["methods"]["baseline"], result["methods"]["random_forest"]
    enough = base["count"] >= SELECTION_RULE["minimum_paired_observations"] and result["counts"]["paired_origin_count"] >= SELECTION_RULE["minimum_origins"]
    selected = "baseline"
    reason = "insufficient_comparable_validation_evidence"
    if enough:
        selected = "random_forest" if forest["mae_units"] < base["mae_units"] - SELECTION_RULE["tie_tolerance"] else "baseline"
        reason = "lower_validation_mae" if abs(forest["mae_units"] - base["mae_units"]) > SELECTION_RULE["tie_tolerance"] else "validation_tie_baseline_preferred"
    return {"selected_method": selected, "reason": reason, "criterion": SELECTION_RULE,
            "paired_observations": base["count"], "paired_origins": result["counts"]["paired_origin_count"],
            "baseline_mae_units": base["mae_units"], "random_forest_mae_units": forest["mae_units"]}
