"""Local API/AppTest measurements on an isolated copy; never changes saved data."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
from statistics import median
import sys
from time import perf_counter
from unittest.mock import patch

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('label')
parser.add_argument('--source', type=Path, default=WORKSPACE_ROOT, help='Optional original source snapshot for a matched before measurement')
args = parser.parse_args()
sys.path.insert(0, str(args.source.resolve()))
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest
from app.api.main import create_app
from app.core.config import Settings
from app.ui.api_client import APIClient
from app.ui.common import saved_get, saved_download


def fingerprint():
    files = [WORKSPACE_ROOT / "runtime/restockiq.sqlite3"]
    files += [p for root in ("data", "artifacts") for p in (WORKSPACE_ROOT / root).rglob("*") if p.is_file()]
    return {str(p.relative_to(WORKSPACE_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def main():
    root = WORKSPACE_ROOT / "runtime/refinement-benchmark" / args.label
    root.mkdir(parents=True, exist_ok=True)
    target = root / "restockiq.sqlite3"
    if target.exists():
        raise SystemExit("Choose a new label to avoid replacing a benchmark database")
    before = fingerprint()
    with sqlite3.connect(f"file:{(WORKSPACE_ROOT / 'runtime/restockiq.sqlite3').as_posix()}?mode=ro", uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)
    settings = Settings(data_dir=WORKSPACE_ROOT / 'data', artifact_dir=WORKSPACE_ROOT / 'artifacts', runtime_dir=root)
    result = {'conditions': 'TestClient on copied saved database; first timed request after discovery + median of nine repeats (ms). SQLite warmed by discovery. AppTest cold/warm data cache; no browser measurement.'}
    (root / 'source-fingerprints.json').write_text(json.dumps(before, indent=2), encoding='utf-8')
    with TestClient(create_app(settings)) as backend:
        api = "/api/v1/"
        datasets = backend.get(api + "datasets").json()
        ds = next(d for d in datasets if d["provenance"] == "synthetic")
        runs = backend.get(api + "recommendations", params={"dataset_id": ds["id"]}).json()
        base = next(r for r in runs if not r.get("scenario_id"))
        full = backend.get(api + "recommendations/" + base["id"]).json()
        result['base_products_sha256'] = hashlib.sha256(json.dumps(full['products'], sort_keys=True).encode()).hexdigest()
        sku = full["products"][0]["sku"]
        result["dataset_id"], result["run_id"] = ds["id"], base["id"]
        def measure(name, fn):
            times = []
            for _ in range(10):
                start = perf_counter()
                response = fn()
                times.append((perf_counter() - start) * 1000)
                assert response.is_success, response.text
            result[name] = {"cold_ms": round(times[0], 3), "warm_median_ms": round(median(times[1:]), 3),
                            "bytes": len(response.content)}
            if name == 'scenario_calculation':
                result['scenario_products_sha256'] = hashlib.sha256(json.dumps(response.json()['products'], sort_keys=True).encode()).hexdigest()
        measure("overview", lambda: backend.get(api + f"recommendations/{base['id']}/overview"))
        measure("product", lambda: backend.get(api + f"recommendations/{base['id']}/products/{sku}"))
        scenario = backend.post(api + "scenarios", json={"dataset_id": ds["id"], "forecast_run_id": base["forecast_run_id"],
            "name": "Isolated benchmark", "fx_change_pct": "10", "extra_lead_days": 7}).json()
        measure("scenario_calculation", lambda: backend.post(api + "recommendations", json={"dataset_id": ds["id"],
            "forecast_run_id": base["forecast_run_id"], "scenario_id": scenario["id"], "pricing": full["effective_settings"]["pricing"]}))
        calls = []
        sizes = []
        def request(self, method, path, **kwargs):
            calls.append(f"{method} {path}")
            response = backend.request(method, api + path, **kwargs)
            sizes.append(len(response.content))
            assert response.is_success, response.text
            return response
        saved_get.clear()
        saved_download.clear()
        with patch.object(APIClient, "request", request):
            app = AppTest.from_file(str(args.source / 'app/ui/streamlit_app.py'), default_timeout=30)
            # Compare the exact same saved base, independent of discovery defaults.
            for key, value in {'api_url': 'http://127.0.0.1:8000', 'previous_api': 'http://127.0.0.1:8000',
                'dataset': ds['id'], 'previous_dataset': ds['id'], 'model': base['model_id'], 'previous_model': base['model_id'],
                'forecast': base['forecast_run_id'], 'previous_forecast': base['forecast_run_id'], 'run': base['id']}.items():
                app.session_state[key] = value
            app.run()
            assert not app.exception, app.exception
            result["overview_cold_calls"] = list(calls)
            result['overview_cold_bytes'] = sum(sizes)
            calls.clear()
            sizes.clear()
            app.run()
            assert not app.exception, app.exception
            result["overview_warm_calls"] = list(calls)
            result['overview_warm_bytes'] = sum(sizes)
            calls.clear()
            app.radio(key="page").set_value("Products").run()
            assert not app.exception, app.exception
            result["product_navigation_calls"] = list(calls)
            calls.clear()
            app.radio(key="page").set_value("Model Performance").run()
            assert not app.exception, app.exception
            result["model_navigation_calls"] = list(calls)
    result["source_files_unchanged"] = before == fingerprint()
    assert result["source_files_unchanged"]
    (root / "measurements.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
