from datetime import date, timedelta
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
import pytest

from app.ml.baseline import baseline
from app.ml.features import (FEATURES, HistoryState, eligibility, historical_features,
                             prepare_history, training_rows)


def frames(days=100, sku="A"):
    start = date(2025, 1, 1)
    sales = pd.DataFrame([{"sku": sku, "date": start + timedelta(days=i), "units_sold": 5 + i % 11,
                           "promo_active": i % 9 == 0, "fully_in_stock": True, "store_open": True}
                          for i in range(days)])
    products = pd.DataFrame([{"sku": sku, "category": "cables"}])
    return sales, products


def test_future_and_target_sales_do_not_enter_predictors():
    sales, products = frames()
    target = sales.date.iloc[60]
    original = historical_features(prepare_history(sales, products))
    changed = sales.copy()
    changed.loc[changed.date >= target, "units_sold"] = 999999
    changed.loc[changed.date > target, ["store_open", "fully_in_stock", "promo_active"]] = False
    altered = historical_features(prepare_history(changed, products))
    assert_frame_equal(original.loc[original.date <= target, FEATURES], altered.loc[altered.date <= target, FEATURES])
    assert original.loc[60, "lag_1"] == sales.loc[59, "units_sold"]


def test_forward_proxy_lookback_and_no_backward_fill():
    sales, products = frames(65)
    sales.loc[0:2, "fully_in_stock"] = False
    sales.loc[10, ["store_open", "fully_in_stock"]] = False
    sales.loc[11:50, "fully_in_stock"] = False
    prepared = prepare_history(sales, products)
    assert prepared.loc[0:2, "proxy"].isna().all()
    assert prepared.loc[0:2, "proxy_missing"].eq(1).all()
    expected = sales.loc[3:9, "units_sold"].mean()
    assert prepared.loc[10, "proxy"] == expected
    assert np.isnan(prepared.loc[40, "proxy"])  # No prior valid sales in 28 days.
    assert prepared.loc[51, "proxy"] == sales.loc[51, "units_sold"]
    rows, info = training_rows(prepared, sales.date.iloc[-1])
    assert rows.observed.all()
    assert not rows.date.isin(sales.loc[~sales.fully_in_stock, "date"]).any()
    assert not rows.date.isin(sales.loc[~sales.store_open, "date"]).any()


def test_training_and_recursive_feature_logic_match():
    sales, products = frames()
    sales.loc[40, "fully_in_stock"] = False
    prepared = prepare_history(sales, products)
    feature_rows = historical_features(prepared)
    for target_index in (35, 41, 70):
        history = prepared.iloc[:target_index]
        state = HistoryState.from_frame("A", "cables", history)
        actual = state.features(sales.loc[target_index, "date"], promo=sales.loc[target_index, "promo_active"])
        expected = feature_rows.iloc[target_index]
        for name in FEATURES:
            if name in ("sku", "category"):
                assert actual[name] == expected[name]
            else:
                assert actual[name] == pytest.approx(float(expected[name]), abs=1e-12)


def test_baseline_last_seven_observed_within_28_calendar_days():
    sales, products = frames(40)
    origin = sales.date.iloc[-1]
    sales.loc[30:39, "fully_in_stock"] = False
    sales.loc[28, ["store_open", "fully_in_stock"]] = False
    valid = sales[(sales.date >= origin - timedelta(days=27)) & sales.fully_in_stock & sales.store_open].tail(7)
    result = baseline(sales, origin)
    assert result["expected_units"] == valid.units_sold.mean()
    assert result["observation_count"] == 7
    sales.loc[:, "fully_in_stock"] = False
    sales.loc[25:27, "fully_in_stock"] = True
    result = baseline(sales, origin)
    assert result["observation_count"] == 3
    assert "baseline_fewer_than_seven_observations" in result["reason_codes"]
    sales.loc[:, "fully_in_stock"] = False
    assert baseline(sales, origin)["expected_units"] is None


def test_eligibility_is_at_cutoff_and_features_allowlist():
    sales, products = frames()
    prepared = prepare_history(sales.assign(latent_demand=1000, selling_price_pkr=12, on_hand_units=0), products)
    assert not eligibility(prepared, sales.date.iloc[58])["A"]["eligible"]
    assert eligibility(prepared, sales.date.iloc[59])["A"]["eligible"]
    assert not {"latent_demand", "selling_price_pkr", "on_hand_units"} & set(prepared.columns)
