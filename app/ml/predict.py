from datetime import timedelta
import numpy as np
import pandas as pd

from app.core.errors import StateConflict
from app.ml.baseline import baseline
from app.ml.features import FEATURES, REQUIRED_HISTORY, HistoryState, eligibility, prepare_history


def predict_recursive(sales, products, origin, horizon, candidate, selected_method):
    """Pure forecasting: never inspects targets, stock or flags after origin."""
    if not 1 <= horizon <= 30:
        raise ValueError("horizon_days must be between 1 and 30")
    if candidate["training_cutoff"] > origin:
        raise StateConflict("Model was trained after requested origin; use an eligible evaluation artifact or retrain at that cutoff")
    history = prepare_history(sales, products, through=origin)
    info = eligibility(history, origin) if not history.empty else {}
    rows, statuses, states = [], [], {}
    trained = set(candidate["trained_skus"])
    for product in products.sort_values("sku").itertuples(index=False):
        group = history[history.sku == product.sku] if not history.empty else history
        base = baseline(group, origin) if not group.empty else {"expected_units": None, "observation_count": 0, "reason_codes": ["insufficient_history"]}
        reasons = list(base["reason_codes"])
        method = selected_method
        state = HistoryState.from_frame(product.sku, product.category, group) if not group.empty else None
        if method == "random_forest":
            if product.sku not in trained:
                reasons.append("unseen_or_untrained_product")
            if not info.get(product.sku, {}).get("eligible", False):
                reasons.append("minimum_history_not_met")
            feature = state.features(origin + timedelta(days=1)) if state else {}
            if any(not np.isfinite(feature.get(k, np.nan)) for k in REQUIRED_HISTORY):
                reasons.append("incomplete_feature_history")
            if candidate["pipeline"] is None:
                reasons.append("random_forest_unavailable")
            if any(r in reasons for r in ("unseen_or_untrained_product", "minimum_history_not_met", "incomplete_feature_history", "random_forest_unavailable")):
                method = "baseline"
        fallback = selected_method == "random_forest" and method != "random_forest"
        insufficient = method == "baseline" and base["expected_units"] is None
        status = {"sku": product.sku, "status": "insufficient_history" if insufficient else "forecasted",
                  "method": method if not insufficient else None, "fallback_applied": fallback,
                  "reason_codes": reasons, "observation_count": base["observation_count"]}
        statuses.append(status)
        if not insufficient:
            states[product.sku] = (state, base, status)
    for step in range(1, horizon + 1):
        target = origin + timedelta(days=step)
        model_skus = [sku for sku, (_, _, status) in states.items() if status["method"] == "random_forest"]
        estimates = {}
        if model_skus:
            frame = pd.DataFrame([states[sku][0].features(target, promo=False) for sku in model_skus], columns=FEATURES)
            predictions = candidate["pipeline"].predict(frame)
            for sku, value in zip(model_skus, predictions):
                if not np.isfinite(value):
                    raise StateConflict("Model produced nonfinite expected demand; retrain the model")
                estimates[sku] = max(0.0, float(value))
        for sku, (state, base, status) in states.items():
            value = estimates[sku] if status["method"] == "random_forest" else base["expected_units"]
            rows.append({"sku": sku, "date": target, "expected_units": value, "method": status["method"],
                         "fallback_applied": status["fallback_applied"], "reason_codes": status["reason_codes"],
                         "observation_count": status["observation_count"]})
            if state:
                state.append_prediction(value)
    return rows, statuses
