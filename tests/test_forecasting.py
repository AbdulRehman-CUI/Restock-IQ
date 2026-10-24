from datetime import date, timedelta
from unittest.mock import patch
import numpy as np
import pandas as pd
import pytest

from app.core.errors import ArtifactError, StateConflict
from app.ml.artifacts import dependency_versions, load_artifact, save_artifact
from app.ml.evaluate import evaluate_partition, scores, select_method, split_dates
from app.ml.features import FEATURE_VERSION, prepare_history
from app.ml.predict import predict_recursive
from app.ml.train import fit_candidate
from test_features import frames


@pytest.fixture(scope="module")
def candidate():
    sales, products = frames(120)
    cutoff = sales.date.iloc[83]
    return fit_candidate(prepare_history(sales, products), cutoff)


def test_fixed_origin_ignores_changed_heldout_data(candidate):
    sales, products = frames(120)
    origin = sales.date.iloc[83]
    before = predict_recursive(sales, products, origin, 30, candidate, "random_forest")
    altered = sales.copy()
    altered.loc[altered.date > origin, "units_sold"] = 9999999
    altered.loc[altered.date > origin, ["fully_in_stock", "promo_active", "store_open"]] = False
    after = predict_recursive(altered, products, origin, 30, candidate, "random_forest")
    np.testing.assert_allclose([r["expected_units"] for r in before[0]], [r["expected_units"] for r in after[0]], rtol=0, atol=1e-12)
    assert len(before[0]) == 30
    assert before[0][0]["date"] == origin + timedelta(days=1)
    assert before[0][-1]["date"] == origin + timedelta(days=30)
    assert all(r["expected_units"] >= 0 and r["method"] == "random_forest" for r in before[0])


def test_recursive_step_uses_previous_prediction_and_future_flags_assumption():
    class RecordingPipeline:
        def __init__(self):
            self.features = []
        def predict(self, features):
            self.features.append(features.copy())
            return features.lag_1.to_numpy() + 0.25
    sales, products = frames()
    spy = RecordingPipeline()
    candidate = {"pipeline": spy, "trained_skus": ["A"], "training_cutoff": sales.date.iloc[70]}
    rows, _ = predict_recursive(sales, products, sales.date.iloc[80], 3, candidate, "random_forest")
    assert rows[1]["expected_units"] == rows[0]["expected_units"] + 0.25
    assert spy.features[1].lag_1.iloc[0] == rows[0]["expected_units"]
    assert all(not feature.promo_active.any() for feature in spy.features)
    assert all(feature.availability_28.iloc[0] == 1 for feature in spy.features)


def test_sparse_unknown_and_unobservable_products_fall_back(candidate):
    sales, products = frames(120)
    unknown = sales.copy().assign(sku="UNKNOWN")
    sparse, _ = frames(10, "SPARSE")
    # Align the short series to origin so its baseline observations are recent.
    sparse["date"] = [sales.date.iloc[83] - timedelta(days=9 - i) for i in range(10)]
    unseen = sales.copy().assign(sku="EMPTY", fully_in_stock=False)
    all_sales = pd.concat([sales, unknown, sparse, unseen], ignore_index=True)
    all_products = pd.DataFrame([{"sku": sku, "category": "new_category" if sku == "UNKNOWN" else "cables"}
                                 for sku in ("A", "UNKNOWN", "SPARSE", "EMPTY")])
    rows, statuses = predict_recursive(all_sales, all_products, sales.date.iloc[83], 7, candidate, "random_forest")
    status = {r["sku"]: r for r in statuses}
    assert status["UNKNOWN"]["method"] == "baseline" and status["UNKNOWN"]["fallback_applied"]
    assert "unseen_or_untrained_product" in status["UNKNOWN"]["reason_codes"]
    assert status["SPARSE"]["method"] == "baseline"
    assert status["EMPTY"]["status"] == "insufficient_history"
    assert len(rows) == 21
    baseline_values = [r["expected_units"] for r in rows if r["sku"] == "UNKNOWN"]
    assert len(set(baseline_values)) == 1


def test_historical_model_rejection_and_reproducible_fit(candidate):
    sales, products = frames(120)
    with pytest.raises(StateConflict):
        predict_recursive(sales, products, sales.date.iloc[80], 1, candidate, "baseline")
    repeated = fit_candidate(prepare_history(sales, products), sales.date.iloc[83])
    first, _ = predict_recursive(sales, products, sales.date.iloc[83], 2, candidate, "random_forest")
    second, _ = predict_recursive(sales, products, sales.date.iloc[83], 2, repeated, "random_forest")
    np.testing.assert_allclose([r["expected_units"] for r in first], [r["expected_units"] for r in second], atol=1e-12)


def test_artifact_reload_integrity_and_location(settings, candidate):
    from uuid import uuid4
    identifier, dataset_id = str(uuid4()), str(uuid4())
    payload = {**candidate, "model_id": identifier}
    metadata = {"model_id": identifier, "dataset_id": dataset_id, "feature_version": FEATURE_VERSION,
                "dependencies": dependency_versions()}
    files = save_artifact(settings, identifier, payload, metadata)
    record = {"id": identifier, "dataset_id": dataset_id, "status": "completed",
              "training_cutoff": candidate["training_cutoff"], **files}
    loaded = load_artifact(settings, record)
    sales, products = frames(120)
    origin = candidate["training_cutoff"]
    one, _ = predict_recursive(sales, products, origin, 3, candidate, "random_forest")
    two, _ = predict_recursive(sales, products, origin, 3, loaded, "random_forest")
    np.testing.assert_allclose([r["expected_units"] for r in one], [r["expected_units"] for r in two], atol=1e-12)
    with pytest.raises(ArtifactError):
        load_artifact(settings, {**record, "artifact_path": "C:/untrusted/pipeline.joblib"})
    from pathlib import Path
    with Path(files["artifact_path"]).open("ab") as handle:
        handle.write(b"tampered")
    with patch("app.ml.artifacts.joblib.load") as unsafe_load, pytest.raises(ArtifactError):
        load_artifact(settings, record)
    unsafe_load.assert_not_called()


def test_zero_wape_and_honest_cumulative_coverage(candidate):
    assert scores([0, 0], [1, 2])["wape"] is None
    assert scores([0, 0], [1, 2])["wape_status"] == "undefined_zero_actual_units"
    assert scores([], [])["status"] == "insufficient_coverage"
    too_few = scores([10, 20], [12, 18], minimum_count=3)
    assert too_few["count"] == 2 and too_few["mae_units"] is None
    assert too_few["status"] == "insufficient_coverage"
    sales, products = frames(120)
    sales.loc[84, "fully_in_stock"] = False
    result = evaluate_partition(sales, products, candidate, sales.date.iloc[84], sales.date.iloc[90])
    h7 = result["7"]
    assert h7["methods"]["baseline"]["count"] == h7["methods"]["random_forest"]["count"] == 6
    assert h7["cumulative_demand"]["methods"]["baseline"]["count"] == 0
    assert h7["counts"]["excluded_incomplete_windows"] == 1
    assert result["30"]["methods"]["baseline"]["status"] == "insufficient_coverage"


def test_selection_is_validation_only_and_conservative():
    validation = {"7": {"methods": {"baseline": {"count": 100, "mae_units": 2.0},
                                   "random_forest": {"count": 100, "mae_units": 1.0}},
                        "counts": {"paired_origin_count": 3}}}
    assert select_method(validation)["selected_method"] == "random_forest"
    validation["7"]["methods"]["random_forest"]["mae_units"] = 2.0
    assert select_method(validation)["selected_method"] == "baseline"
    validation["7"]["methods"]["random_forest"]["mae_units"] = 0.1
    validation["7"]["counts"]["paired_origin_count"] = 1
    assert select_method(validation)["selected_method"] == "baseline"
    # Function accepts no test metrics; changed test outcomes have no selection input.
    assert "test" not in __import__("inspect").signature(select_method).parameters


def test_split_dates_and_preprocessing_fitted_at_cutoff(candidate):
    sales, products = frames(120)
    split = split_dates(sales.date)
    assert split["train"][1] == sales.date.iloc[83]
    assert split["validation"][1] == sales.date.iloc[101]
    assert split["test"][0] == sales.date.iloc[102]
    encoder = candidate["pipeline"].named_steps["preprocessing"].named_transformers_["categories"]
    assert list(encoder.categories_[0]) == ["A"]
    assert candidate["training_row_count"] == 56
