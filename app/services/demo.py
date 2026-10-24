"""Explicit, append-only demo preparation; never invoked during UI rendering."""
import time
from datetime import date
from app.services.dashboard import discover
from app.services.jobs import JobWorker
from app.db.repositories import get_job
from app.data.synthetic import GeneratorConfig
from app.services.forecasting import create_forecast
from app.services.recommendations import create_recommendations
from app.services.scenarios import create_scenario, get_scenario, ScenarioAssumptions


def prepare_demo(db, settings):
    worker = JobWorker(db, settings)
    def wait(job):
        while job["status"] not in ("completed", "failed"):
            time.sleep(.2)
            job = get_job(db, job["id"])
        if job["status"] == "failed":
            raise ValueError(job["error"])
        return job
    try:
        datasets = [d for d in discover(db, "datasets") if d["provenance"] == "synthetic" and
                    (d["end_date"] - d["start_date"]).days >= 364]
        if datasets:
            dataset = datasets[0]
        else:
            job = wait(worker.submit_demo(GeneratorConfig()))
            dataset = next(d for d in discover(db, "datasets") if d["id"] == job["dataset_id"])
        ds = dataset["id"]
        models = [m for m in discover(db, "models", dataset_id=ds) if m["status"] == "completed" and m["training_cutoff"] == dataset["end_date"]]
        model_id = models[0]["id"] if models else wait(worker.submit_training(ds))["model_id"]
        forecasts = [f for f in discover(db, "forecasts", dataset_id=ds, model_id=model_id)
                     if f["origin"] == dataset["end_date"] and f["horizon_days"] == 30]
        forecast_id = forecasts[0]["id"] if forecasts else create_forecast(db, settings, ds, model_id, dataset["end_date"], 30)["forecast_run_id"]
        runs = discover(db, "recommendations", dataset_id=ds, forecast_run_id=forecast_id)
        bases = [r for r in runs if r["scenario_id"] is None]
        base_id = bases[0]["id"] if bases else create_recommendations(db, settings, ds, forecast_id)["recommendation_run_id"]
        from app.services.recommendations import get_recommendations
        from app.services.pricing import PricingSettings
        pricing = get_recommendations(db, base_id)["effective_settings"]["pricing"]
        assumptions = ScenarioAssumptions(fx_change_pct=10, extra_lead_days=7, target_margin_override=".20")
        scenarios = discover(db, "scenarios", dataset_id=ds, forecast_run_id=forecast_id)
        matching = [s for s in scenarios if ScenarioAssumptions.model_validate(get_scenario(db, s["id"])["assumptions"]) == assumptions]
        scenario_id = matching[0]["id"] if matching else create_scenario(db, ds, forecast_id, "FX +10%, lead +7 days, margin 20%", assumptions)["id"]
        scenario_runs = [r for r in runs if r["scenario_id"] == scenario_id and
                         get_recommendations(db, r["id"])["effective_settings"]["pricing"] == pricing]
        scenario_run_id = scenario_runs[0]["id"] if scenario_runs else create_recommendations(db, settings, ds, forecast_id, scenario_id, PricingSettings(**pricing))["recommendation_run_id"]
        return {"dataset_id": ds, "model_id": model_id, "forecast_run_id": forecast_id,
                "base_run_id": base_id, "scenario_id": scenario_id, "scenario_run_id": scenario_run_id}
    finally:
        worker.close()
