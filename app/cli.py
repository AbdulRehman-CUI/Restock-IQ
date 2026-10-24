import argparse
from datetime import date
import json
import sys
import time

from app.core.config import Settings, resolve_path
from app.core.errors import DatasetValidationError, NotFound, StateConflict, JobConflict
from app.data.synthetic import GeneratorConfig, generate_demo
from app.db.repositories import get_summary
from app.db.session import Database
from app.services.ingestion import import_dataset


def main(argv=None):
    parser = argparse.ArgumentParser(prog="restockiq", description="RestockIQ offline data and demand forecasting")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="Create the local schema")
    generate = commands.add_parser("generate-demo", help="Export a synthetic CSV batch into a new UUID directory")
    generate.add_argument("--seed", type=int, default=42)
    generate.add_argument("--start-date", type=date.fromisoformat, default=date(2025, 10, 16))
    generate.add_argument("--days", type=int, default=365)
    generate.add_argument("--sku-count", type=int, default=20)
    generate.add_argument("--import", dest="import_generated", action="store_true", help="Also atomically import the generated batch")
    importer = commands.add_parser("import", help="Import exactly the six operational CSVs; provenance is uploaded")
    importer.add_argument("directory")
    summary = commands.add_parser("summary")
    summary.add_argument("dataset_id")
    api = commands.add_parser("serve")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    ui = commands.add_parser("ui", help="Start the local Streamlit dashboard (API runs separately)")
    ui.add_argument("--port", type=int, default=8501)
    ui.add_argument("--api-url", help="Defaults to RESTOCKIQ_API_URL or http://127.0.0.1:8000")
    commands.add_parser("prepare-demo", help="Reuse a suitable analysis and explicitly create missing demo prerequisites")
    commands.add_parser("test", help="Run the project's pytest suite")
    training = commands.add_parser("train", help="Train, evaluate, select, and save final/evaluation models via the local worker")
    training.add_argument("dataset_id")
    training.add_argument("--seed", type=int, default=42)
    metrics = commands.add_parser("metrics")
    metrics.add_argument("model_id")
    forecast = commands.add_parser("forecast")
    forecast.add_argument("dataset_id")
    forecast.add_argument("model_id")
    forecast.add_argument("--origin", type=date.fromisoformat, required=True)
    forecast.add_argument("--horizon-days", type=int, choices=range(1, 31), required=True)
    export = commands.add_parser("export-forecast")
    export.add_argument("forecast_run_id")
    export.add_argument("output", help="New CSV path; never overwrite an existing export")
    scenario = commands.add_parser("create-scenario")
    scenario.add_argument("dataset_id")
    scenario.add_argument("forecast_run_id")
    scenario.add_argument("--name", required=True)
    scenario.add_argument("--fx-change-pct", default="0")
    scenario.add_argument("--supplier-price-change-pct", default="0")
    scenario.add_argument("--additional-freight-pkr", default="0")
    scenario.add_argument("--extra-lead-days", type=int, default=0)
    scenario.add_argument("--target-margin")
    recommend = commands.add_parser("recommend")
    recommend.add_argument("dataset_id")
    recommend.add_argument("forecast_run_id")
    recommend.add_argument("--scenario-id")
    recommend.add_argument("--price-step-pkr", default="5")
    recommend.add_argument("--max-price-change-pct")
    inspect_run = commands.add_parser("recommendation")
    inspect_run.add_argument("run_id")
    inspect_run.add_argument("--sku")
    compare = commands.add_parser("compare-runs")
    compare.add_argument("base_run_id")
    compare.add_argument("scenario_run_id")
    export_recs = commands.add_parser("export-recommendations")
    export_recs.add_argument("run_id")
    export_recs.add_argument("output")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    if args.command == "ui":
        import os
        import subprocess
        from app.core.config import PROJECT_ROOT
        env = dict(os.environ, RESTOCKIQ_API_URL=args.api_url or os.getenv("RESTOCKIQ_API_URL", "http://127.0.0.1:8000"))
        return subprocess.call([sys.executable, "-m", "streamlit", "run", str(PROJECT_ROOT / "app/ui/streamlit_app.py"),
            "--server.port", str(args.port), "--server.address", "127.0.0.1", "--server.headless", "true"], cwd=PROJECT_ROOT, env=env)
    if args.command == "serve":
        import uvicorn
        uvicorn.run("app.api.main:app", host=args.host, port=args.port, workers=1)
        return 0
    if args.command == "test":
        import pytest
        from app.core.config import PROJECT_ROOT
        return pytest.main([str(PROJECT_ROOT / "tests"), "-q"])
    db = Database(settings.runtime_dir / "restockiq.sqlite3")
    db.initialize()
    try:
        if args.command == "init-db":
            result = {"database": str(settings.runtime_dir / "restockiq.sqlite3"), "initialized": True}
        elif args.command == "prepare-demo":
            from app.services.demo import prepare_demo
            result = prepare_demo(db, settings)
        elif args.command == "generate-demo":
            config = GeneratorConfig(seed=args.seed, start_date=args.start_date, days=args.days, sku_count=args.sku_count)
            generated = generate_demo(settings, config)
            result = {"dataset_id": generated.dataset_id, "operational_dir": str(generated.operational_dir),
                      "evaluation_dir": str(generated.evaluation_dir), "manifest": generated.manifest}
            if args.import_generated:
                result["imported_summary"] = import_dataset(db, settings, generated.operational_dir,
                    dataset_id=generated.dataset_id, synthetic=True, configuration=config.model_dump(mode="json"),
                    manifest_path=generated.operational_dir / "manifest.json")
        elif args.command == "import":
            result = import_dataset(db, settings, resolve_path(args.directory))
        elif args.command == "train":
            from app.services.jobs import JobWorker
            from app.db.repositories import get_job
            from app.services.forecasting import model_metrics
            worker = JobWorker(db, settings)
            try:
                job = worker.submit_training(args.dataset_id, args.seed)
                while job["status"] not in ("failed", "completed"):
                    time.sleep(0.2)
                    job = get_job(db, job["id"])
                if job["status"] == "failed":
                    print(json.dumps(job, indent=2), file=sys.stderr)
                    return 2
                result = {"job": job, "model": model_metrics(db, job["model_id"])}
            finally:
                worker.close()
        elif args.command == "metrics":
            from app.services.forecasting import model_metrics
            result = model_metrics(db, args.model_id)
        elif args.command == "forecast":
            from app.services.forecasting import create_forecast
            result = create_forecast(db, settings, args.dataset_id, args.model_id, args.origin, args.horizon_days)
        elif args.command == "export-forecast":
            from app.services.forecasting import export_forecast
            result = export_forecast(db, args.forecast_run_id, resolve_path(args.output))
        elif args.command == "create-scenario":
            from app.services.scenarios import ScenarioAssumptions, create_scenario
            assumptions = ScenarioAssumptions(fx_change_pct=args.fx_change_pct,
                supplier_price_change_pct=args.supplier_price_change_pct, additional_freight_pkr=args.additional_freight_pkr,
                extra_lead_days=args.extra_lead_days, target_margin_override=args.target_margin)
            result = create_scenario(db, args.dataset_id, args.forecast_run_id, args.name, assumptions)
        elif args.command == "recommend":
            from app.services.recommendations import create_recommendations
            from app.services.pricing import PricingSettings
            result = create_recommendations(db, settings, args.dataset_id, args.forecast_run_id, args.scenario_id,
                PricingSettings(price_step_pkr=args.price_step_pkr, max_price_change_pct=args.max_price_change_pct))
        elif args.command == "recommendation":
            from app.services.recommendations import get_product, get_recommendations
            result = get_product(db, args.run_id, args.sku) if args.sku else get_recommendations(db, args.run_id)
        elif args.command == "compare-runs":
            from app.services.recommendations import compare_runs
            result = compare_runs(db, args.base_run_id, args.scenario_run_id)
        elif args.command == "export-recommendations":
            from app.services.exports import export_recommendations
            result = export_recommendations(db, args.run_id, resolve_path(args.output))
        else:
            result = get_summary(db, args.dataset_id)
        print(json.dumps(result, indent=2, default=str))
        return 0
    except DatasetValidationError as exc:
        print(json.dumps({"code": "dataset_validation_failed", **exc.issues.payload()}, indent=2), file=sys.stderr)
        return 2
    except (ValueError, NotFound, OSError, StateConflict, JobConflict) as exc:
        print(json.dumps({"code": "command_failed", "message": str(exc)}, indent=2), file=sys.stderr)
        return 2
    finally:
        db.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
