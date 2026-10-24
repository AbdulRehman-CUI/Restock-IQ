"""Explicit local acceptance check. Appends one scenario/run and one review.

Uses supported Streamlit AppTest with real HTTP calls; this is not visual/browser QA.
Run with the API and UI already running. No browser rendering is claimed.
"""
import argparse
import json
import csv
import io
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from streamlit.testing.v1 import AppTest
from app.ui.api_client import APIClient
from app.ui.common import saved_get, saved_download


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--model-id")
    parser.add_argument("--forecast-id")
    parser.add_argument("--base-run-id")
    args = parser.parse_args()
    os.environ["RESTOCKIQ_API_URL"] = args.api_url
    api = APIClient(args.api_url)
    saved_get.clear()
    saved_download.clear()
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app/ui/streamlit_app.py"), default_timeout=45).run()
    def healthy():
        assert not app.exception, app.exception
        assert not app.error, [e.value for e in app.error]
    healthy()
    for key, value in (("model", args.model_id), ("forecast", args.forecast_id), ("run", args.base_run_id)):
        if value:
            app.selectbox(key=key).set_value(value).run()
            healthy()
    base_id = app.session_state["run"]
    base = api.get(f"recommendations/{base_id}")
    forecast_id = base["forecast_run_id"]
    forecast = api.get(f"forecasts/{forecast_id}")
    assert len(app.metric) == 4
    assert len(app.get("download_button")) == 4
    for resource, id in (("forecasts", forecast_id), ("recommendations", base_id)):
        rows = list(csv.DictReader(io.StringIO(api.download(f"{resource}/{id}/export").decode("utf-8"))))
        assert rows and all(r["forecast_run_id" if resource == "forecasts" else "recommendation_run_id"] == id for r in rows)
        metadata = api.get(f"{resource}/{id}/metadata")
        assert metadata["forecast_run_id" if resource == "forecasts" else "recommendation_run_id"] == id
    next(b for b in app.button if b.label == "Inspect product").click().run()
    healthy()
    assert len(app.get("plotly_chart")) == 2
    sku = app.session_state["product"]
    actions_path = f"recommendations/{base_id}/actions"
    before = len(api.get(actions_path))
    next(t for t in app.text_input if t.label == "Optional note").set_value("Phase 4 live HTTP acceptance check")
    next(b for b in app.button if b.label == "Save local review").click().run()
    healthy()
    app.run()
    assert len(api.get(actions_path)) == before + 1
    app.radio(key="page").set_value("Scenario Lab").run()
    healthy()
    next(b for b in app.button if b.label == "Phase 3 demonstration").click().run()
    next(t for t in app.text_input if t.label == "Scenario name").set_value("Phase 4 live UI verification")
    next(b for b in app.button if b.label == "Run scenario").click().run()
    healthy()
    result_id = app.session_state["scenario_result"]
    scenario_run = api.get(f"recommendations/{result_id}")
    comparison = api.get(f"recommendations/{base_id}/compare/{result_id}")
    assert comparison["demand_unchanged"] and len(comparison["products"]) == len(base["products"])
    assert scenario_run["scenario"]["assumptions"]["target_margin_override"] == "0.2"
    run_count = len(api.get("recommendations", dataset_id=base["dataset_id"], forecast_run_id=forecast_id))
    app.run()
    assert len(api.get("recommendations", dataset_id=base["dataset_id"], forecast_run_id=forecast_id)) == run_count
    app.radio(key="page").set_value("Model Evaluation").run()
    healthy()
    next(s for s in app.selectbox if s.label == "Evaluation horizon (days)").set_value(30).run()
    healthy()
    assert any("insufficient_coverage" in str(element.value) for element in app.json)
    app.radio(key="page").set_value("Data & Settings").run()
    healthy()
    # Changing to an unused localhost port tests the UI connection-error path.
    app.text_input(key="api_url").set_value("http://127.0.0.1:1").run()
    assert not app.exception and any("Cannot reach" in e.value for e in app.error)
    app.text_input(key="api_url").set_value(args.api_url).run()
    healthy()
    assert api.get(f"recommendations/{base_id}") == base
    assert api.get(f"forecasts/{forecast_id}") == forecast
    print(json.dumps({"status": "passed", "kind": "AppTest with real HTTP; no browser rendering",
        "api_url": args.api_url, "dataset_id": base["dataset_id"], "model_id": base["model_id"],
        "forecast_run_id": forecast_id, "base_run_id": base_id, "scenario_run_id": result_id,
        "reviewed_sku": sku, "pages_checked": 5}, indent=2))


if __name__ == "__main__":
    main()
