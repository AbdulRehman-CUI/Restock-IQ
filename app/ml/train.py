"""Three distinct fits: validation candidate, test candidate, final inference."""
import logging
from time import perf_counter
from uuid import uuid4

from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from app.core.errors import NotFound
from app.db.models import Job, ModelRun
from app.db.repositories import load_demand_data, model_record
from app.ml.artifacts import dependency_versions, save_artifact
from app.ml.evaluate import ORIGIN_STRIDE, evaluate_partition, select_method, split_dates
from app.ml.features import (ASSUMPTIONS, CATEGORICAL, FEATURE_VERSION, FEATURES, NUMERIC,
                             prepare_history, training_rows)
from app.services.ingestion import utc_now

logger = logging.getLogger(__name__)
RF_PARAMETERS = {"n_estimators": 200, "max_depth": 12, "min_samples_leaf": 3, "n_jobs": -1}


def fit_candidate(prepared, cutoff, seed=42):
    rows, info = training_rows(prepared, cutoff)
    # The raw minimum alone can leave very few post-warmup examples. Require 30
    # usable training targets too; record this conservative extra guard explicitly.
    counts = rows.groupby("sku").size().to_dict()
    trained_skus = sorted(sku for sku, count in counts.items() if count >= 30)
    rows = rows[rows.sku.isin(trained_skus)]
    for sku, item in info.items():
        item["usable_training_targets"] = int(counts.get(sku, 0))
        item["included_in_fit"] = sku in trained_skus
    pipeline = None
    if len(rows):
        preprocessing = ColumnTransformer([
            ("categories", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL),
            ("numeric", "passthrough", NUMERIC)], remainder="drop")
        pipeline = Pipeline([("preprocessing", preprocessing),
                             ("regressor", RandomForestRegressor(**RF_PARAMETERS, random_state=seed))])
        pipeline.fit(rows[FEATURES], rows.units_sold.astype(float))
    return {"pipeline": pipeline, "training_cutoff": cutoff, "trained_skus": trained_skus,
            "feature_version": FEATURE_VERSION, "seed": seed, "eligibility": info,
            "training_row_count": len(rows)}


def coverage(prepared, start, end):
    group = prepared[(prepared.date >= start) & (prepared.date <= end)]
    closed = int((~group.store_open).sum())
    censored = int((group.store_open & ~group.fully_in_stock).sum())
    return {"total_sku_days": len(group), "uncensored_open_targets": int(group.observed.sum()),
            "closed_sku_days": closed, "censored_open_sku_days": censored,
            "excluded_fraction": (closed + censored) / len(group) if len(group) else None}


def train_model(db, settings, dataset_id, seed=42, *, job_id=None, progress=None):
    if not 0 <= seed <= 2**32 - 1:
        raise ValueError("seed must be between 0 and 4294967295")
    sales, products, provenance = load_demand_data(db, dataset_id)
    model_id, started = str(uuid4()), perf_counter()
    created_at = utc_now()
    with db.transaction() as session:
        session.add(ModelRun(id=model_id, dataset_id=str(dataset_id), job_id=job_id,
            role="final_inference", status="running", training_cutoff=provenance["end_date"],
            feature_version=FEATURE_VERSION, selected_method="pending", seed=seed,
            created_at=created_at, details={}))
    def report(percent, stage):
        if progress:
            progress(percent, stage)
    try:
        report(10, "preparing_features")
        prepared = prepare_history(sales, products)
        partitions = split_dates(sales.date)
        report(20, "fitting_validation_candidate")
        validation_candidate = fit_candidate(prepared, partitions["train"][1], seed)
        report(30, "evaluating_validation")
        validation = evaluate_partition(sales, products, validation_candidate, *partitions["validation"])
        selection = select_method(validation)  # No test input exists at this point.
        report(50, "fitting_test_candidate")
        test_candidate = fit_candidate(prepared, partitions["validation"][1], seed)
        report(60, "evaluating_untouched_test")
        test = evaluate_partition(sales, products, test_candidate, *partitions["test"])
        report(80, "fitting_final_inference")
        final_candidate = fit_candidate(prepared, provenance["end_date"], seed)
        details = {
            "feature_names": FEATURES, "assumptions": ASSUMPTIONS,
            "parameters": {**RF_PARAMETERS, "random_state": seed}, "dependencies": dependency_versions(),
            "partitions": {name: {"start": start.isoformat(), "end": end.isoformat()}
                           for name, (start, end) in partitions.items()},
            "coverage": {name: coverage(prepared, *bounds) for name, bounds in partitions.items()},
            "origin_stride_days": ORIGIN_STRIDE,
            "evaluation_windows": "Origins every seven days from partition start minus one; complete horizons only; 30-day windows overlap. Scores count repeated target appearances.",
            "comparison": "Both candidates scored on identical uncensored/open targets where RF actually used the fitted forest; baseline fallbacks excluded from paired scores. All products retained in per-product coverage.",
            "selection": selection, "validation": validation, "test": test,
            "provenance": {**provenance, "start_date": provenance["start_date"].isoformat(), "end_date": provenance["end_date"].isoformat()},
            "extra_fit_guard": "At least 30 feature-complete uncensored training targets per SKU after warmup.",
        }
        report(90, "saving_artifacts")
        artifacts = []
        for name, role, candidate in (("validation", "evaluation", validation_candidate),
                                       ("test", "evaluation", test_candidate),
                                       ("final", "final_inference", final_candidate)):
            artifact_id = model_id if name == "final" else str(uuid4())
            candidate["model_id"] = artifact_id
            metadata = {"model_id": artifact_id, "dataset_id": str(dataset_id), "role": role,
                "evaluation_partition": name if role == "evaluation" else None,
                "training_cutoff": candidate["training_cutoff"].isoformat(),
                "feature_version": FEATURE_VERSION, "seed": seed, "created_at": created_at,
                # Evaluation artifacts serve the fixed RF candidate. Applying a
                # choice learned at validation end to a validation-start origin
                # would leak method selection even though tree fitting was clean.
                "selected_method": selection["selected_method"] if role == "final_inference" else "random_forest",
                "training_row_count": candidate["training_row_count"], "eligibility": candidate["eligibility"],
                "trained_skus": candidate["trained_skus"], **details}
            files = save_artifact(settings, artifact_id, candidate, metadata)
            artifacts.append((artifact_id, role, candidate, metadata, files))
        duration = perf_counter() - started
        evaluation_ids = {name: artifacts[index][0] for index, name in enumerate(("validation", "test"))}
        with db.transaction() as session:
            final_run = session.get(ModelRun, model_id)
            for artifact_id, role, candidate, metadata, files in artifacts:
                if role == "final_inference":
                    run = final_run
                else:
                    run = ModelRun(id=artifact_id, dataset_id=str(dataset_id), parent_model_id=model_id,
                        role=role, status="completed", training_cutoff=candidate["training_cutoff"],
                        feature_version=FEATURE_VERSION, selected_method=selection["selected_method"],
                        seed=seed, created_at=created_at, details={})
                    session.add(run)
                for key, value in files.items():
                    setattr(run, key, value)
                run.status = "completed"
                run.selected_method = metadata["selected_method"]
                run.details = {**metadata, "training_duration_seconds": duration, "evaluation_model_ids": evaluation_ids}
            if job_id:
                job = session.get(Job, job_id)
                job.status, job.stage, job.progress = "completed", "completed", 100
                job.active_key, job.dataset_id, job.updated_at = None, str(dataset_id), utc_now()
        return model_record(db, model_id)
    except Exception:
        error_id = str(uuid4())
        logger.exception("Training failed; model_id=%s error_id=%s", model_id, error_id)
        with db.transaction() as session:
            run = session.get(ModelRun, model_id)
            run.status, run.error_id = "failed", error_id
            run.error = "Training failed. Inspect the local log using error_id and retry."
            run.details = {"training_duration_seconds": perf_counter() - started}
        raise
