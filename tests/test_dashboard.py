from copy import deepcopy
from datetime import date
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch
import time
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update, delete, select, func
from sqlalchemy.exc import IntegrityError
from streamlit.testing.v1 import AppTest

from app.api.main import create_app
from app.core.config import PROJECT_ROOT
from app.db.models import RecommendationAction, RecommendationRun, Scenario
from app.services.recommendations import create_recommendations
from app.services.dashboard import overview
from app.ui.api_client import APIClient, APIError
from app.ui.state import reset_dataset, margin_fraction, selected_exports
from app.ui.common import saved_get, saved_download
from test_scenarios import persisted_fixture


@pytest.fixture
def analysis(db, settings, batch_dir):
    dataset_id, forecast_id = persisted_fixture(db, settings, batch_dir)
    return create_recommendations(db, settings, dataset_id, forecast_id)


def test_discovery_history_metadata_and_actions(settings, analysis):
    run, ds, forecast = analysis["recommendation_run_id"], analysis["dataset_id"], analysis["forecast_run_id"]
    sku = analysis["products"][0]["sku"]
    body = {"request_id": str(uuid4()), "sku": sku, "action": "reviewed", "note": "Local test"}
    with TestClient(create_app(settings)) as client:
        root = "/api/v1/"
        assert client.get(root + "datasets").json()[0]["id"] == ds
        assert client.get(root + "models", params={"dataset_id": ds}).json()[0]["id"] == analysis["model_id"]
        assert client.get(root + "forecasts", params={"dataset_id": ds}).json()[0]["id"] == forecast
        assert client.get(root + "recommendations", params={"dataset_id": ds, "forecast_run_id": forecast}).json()[0]["id"] == run
        assert client.get(root + "scenarios", params={"dataset_id": ds}).json() == []
        contract = client.get(root + "data-contracts").json()["products.csv"]
        assert "sku" in contract["required_fields"] and "target_margin" in contract["optional_fields"]
        assert client.get(root + "models", params={"dataset_id": str(uuid4())}).json() == []
        history = client.get(root + f"datasets/{ds}/products/{sku}/sales", params={"through": "2025-10-20"})
        assert history.status_code == 200
        assert len(history.json()["rows"]) == 5
        assert max(r["date"] for r in history.json()["rows"]) == "2025-10-20"
        assert "latent" not in history.text
        assert client.get(root + f"datasets/{ds}/products/unknown/sales?through=2025-10-20").status_code == 404
        assert client.get(root + f"datasets/{ds}/products/{sku}/sales?through=bad").status_code == 422
        assert "rows" not in client.get(root + f"forecasts/{forecast}/metadata").json()
        assert "products" not in client.get(root + f"recommendations/{run}/metadata").json()
        assert client.get(root + f"recommendations/{run}/overview").json() == overview(analysis["products"])
        path = root + f"recommendations/{run}/actions"
        first = client.post(path, json=body)
        assert first.status_code == 201, first.text
        assert client.post(path, json=body).json() == first.json()
        assert len(client.get(path).json()) == 1
        assert client.post(path, json={**body, "action": "rejected"}).status_code == 409
        assert client.post(path, json={**body, "action": "purchase"}).status_code == 422
        assert client.post(path, json={**body, "request_id": str(uuid4()), "sku": "unknown"}).status_code == 404
        assert client.post(path, json={**body, "note": "x" * 1001}).status_code == 422
        assert client.get(root + f"recommendations/{run}").json() == analysis
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(path).json()[0]["note"] == "Local test"


def test_action_append_only(db, settings, analysis):
    from app.services.dashboard import record_action
    record_action(db, analysis["recommendation_run_id"], uuid4(), analysis["products"][0]["sku"], "rejected", "")
    for statement in (update(RecommendationAction).values(note="changed"), delete(RecommendationAction)):
        with pytest.raises(IntegrityError), db.transaction() as session:
            session.execute(statement)


def test_order_now_partial_and_missing_cash(analysis):
    products = deepcopy(analysis["products"])
    for i, p in enumerate(products):
        p["inventory"]["order_required"] = i < 2
        p["inventory"]["estimated_replenishment_cash_pkr"] = "12.34" if i == 0 else None if i == 1 else "999999.99"
    cash = overview(products)["order_now_cash"]
    assert cash["value"] == "12.34" and cash["included_products"] == cash["excluded_products"] == 1 and cash["partial"]
    products[0]["inventory"]["estimated_replenishment_cash_pkr"] = None
    assert overview(products)["order_now_cash"]["value"] is None
    for p in products:
        p["inventory"]["order_required"] = False
    assert overview(products)["order_now_cash"]["value"] is None


def test_state_percentage_and_export_ids():
    state = {"dataset": "new", "model": "old", "forecast": "old", "run": "old", "product": "old", "scenario_result": "old", "draft_step": 5, "page": "Overview"}
    reset_dataset(state)
    assert state == {"dataset": "new", "page": "Overview"}
    assert margin_fraction(20, True) == "0.2"
    assert margin_fraction(0, True) == "0"
    assert margin_fraction(80, True) == "0.8"
    assert margin_fraction(20, False) is None
    exports = selected_exports("visible-forecast", "visible-run")
    assert len(exports) == 4 and all("visible-" in path and "visible-" in filename for _, path, filename in exports)
    assert selected_exports(None, None) == []


def test_api_client_errors_no_retries_and_downloads():
    calls = []
    def transport(request):
        calls.append(request)
        if request.url.path.endswith("export"):
            return httpx.Response(200, content=b"sku,value\n'=x,1.00\n")
        return httpx.Response(422, json={"code": "invalid", "errors": [{"field": "sku", "message": "missing"}]})
    api = APIClient(transport=httpx.MockTransport(transport))
    with pytest.raises(APIError) as error:
        api.post("scenarios", {})
    assert error.value.status == 422 and len(calls) == 1
    assert api.download("recommendations/visible/export").startswith(b"sku,value")
    assert calls[-1].url.path == "/api/v1/recommendations/visible/export"
    def offline(request):
        raise httpx.ConnectError("offline", request=request)
    with pytest.raises(APIError, match="app.cli serve"):
        APIClient(transport=httpx.MockTransport(offline)).get("datasets")


def app_test():
    saved_get.clear()
    saved_download.clear()
    return AppTest.from_file(str(PROJECT_ROOT / "app/ui/streamlit_app.py"), default_timeout=20)


def test_streamlit_empty_and_disconnected():
    with patch.object(APIClient, "get", return_value=[]):
        app = app_test().run()
        assert not app.exception
        assert any("Create or select" in i.value for i in app.info)
    with patch.object(APIClient, "get", side_effect=APIError("Cannot reach API. Start app.cli serve")):
        app = app_test().run()
        assert not app.exception and "Cannot reach API" in app.error[0].value


def test_streamlit_navigation_forms_and_no_rerun_mutations(settings, analysis, batch_dir):
    with TestClient(create_app(settings)) as backend:
        downloads = []
        def get(self, path, **params):
            r = backend.get("/api/v1/" + path, **({"params": params} if params else {}))
            assert r.is_success, r.text
            return r.json()
        def download(self, path):
            downloads.append(path)
            r = backend.get("/api/v1/" + path)
            assert r.is_success
            return r.content
        def post(self, path, body=None, **kwargs):
            r = backend.post("/api/v1/" + path, json=body, **kwargs)
            assert r.is_success, r.text
            return r.json()
        with patch.object(APIClient, "get", get), patch.object(APIClient, "download", download), patch.object(APIClient, "post", autospec=True, side_effect=post) as writes:
            app = app_test().run()
            assert not app.exception, app.exception
            assert len(app.metric) == 4
            assert set(downloads) == {p for _, p, _ in selected_exports(analysis['forecast_run_id'], analysis['recommendation_run_id'])}
            assert [t.value for t in app.title] == ["Overview"]
            next(s for s in app.multiselect if s.label == "Additional columns").set_value(["Current price", "Replacement cost"]).run()
            frame = app.dataframe[0].value
            assert frame['Current price'].dtype.kind in 'fi'
            assert frame['Replacement cost'].dtype.kind in 'fi'
            query = analysis['products'][0]['sku']
            next(s for s in app.text_input if s.label == 'Search products').set_value(query).run()
            assert list(app.dataframe[0].value['SKU']) == [query]
            app.run()
            assert writes.call_count == 0
            next(b for b in app.button if b.label == "Inspect product").click().run()
            assert not app.exception, app.exception
            assert any(t.value == "Products" for t in app.title)
            assert app.session_state['product'] == query
            next(b for b in app.button if b.label == "Save local review").click().run()
            assert not app.exception, app.exception
            app.run()
            next(b for b in app.button if b.label == "Save local review").click().run()
            assert writes.call_count == 1
            app.radio(key="page").set_value("Scenarios").run()
            assert not app.exception, app.exception
            app.selectbox(key="scenario_preset").set_value("FX +10% / lead +7").run()
            assert writes.call_count == 1
            next(b for b in app.button if b.label == "Run scenario").click().run()
            assert not app.exception, app.exception
            assert writes.call_count == 3

            assert any(m.label == "Order now · scenario" for m in app.metric)
            saved_result = app.session_state["scenario_result"]
            next(b for b in app.button if b.label == "Reset assumptions").click().run()
            assert not app.exception
            assert any("Unapplied changes" in w.value for w in app.warning)
            assert app.session_state["scenario_result"] == saved_result
            assert writes.call_count == 3
            app.selectbox(key='run').set_value(saved_result).run()
            assert not app.exception, app.exception
            assert app.session_state['scenario_result'] == saved_result
            assert app.session_state['saved_scenario'] == backend.get('/api/v1/recommendations/' + saved_result).json()['scenario_id']
            assert f'recommendations/{saved_result}/export' in downloads
            assert backend.get('/api/v1/recommendations/' + analysis['recommendation_run_id']).json() == analysis
            app.run()
            assert writes.call_count == 3
            app.radio(key="page").set_value("Data & Settings").run()
            assert not app.exception, app.exception
            assert writes.call_count == 3
            from app.services.ingestion import import_dataset
            second = import_dataset(backend.app.state.db, settings, batch_dir)
            app.run()
            app.selectbox(key="dataset").set_value(second["dataset_id"]).run()
            assert not app.exception, app.exception
            assert "scenario_result" not in app.session_state
            assert "run" not in app.session_state and "model" not in app.session_state
            assert len(app.metric) == 0


def test_streamlit_empty_database_to_overview(settings):
    with TestClient(create_app(settings)) as backend:
        writes = []
        def request(self, method, path, **kwargs):
            if method == "POST":
                writes.append(path)
            response = backend.request(method, "/api/v1/" + path, **kwargs)
            assert response.is_success, response.text
            return response
        with patch.object(APIClient, "request", request):
            app = app_test().run()
            app.radio(key="page").set_value("Data & Settings").run()
            assert not app.exception
            assert not any(b.label == "Generate forecast" for b in app.button)
            next(b for b in app.button if b.label == 'Validate and import dataset').click().run()
            assert not app.exception and any('missing files' in e.value for e in app.error)
            assert writes == []
            next(n for n in app.number_input if n.label == "Product count").set_value(5)
            next(b for b in app.button if b.label == "Generate demo dataset").click().run()
            def finish_job():
                deadline = time.monotonic() + 90
                while "job" in app.session_state and time.monotonic() < deadline:
                    time.sleep(.1)
                    app.run()
                assert "job" not in app.session_state
                assert not app.exception, app.exception
            finish_job()
            assert app.session_state["dataset"]
            next(b for b in app.button if b.label == "Train and evaluate").click().run()
            finish_job()
            assert app.session_state["model"]
            next(b for b in app.button if b.label == "Generate forecast").click().run()
            assert not app.exception, app.exception
            next(b for b in app.button if b.label == "Generate base recommendations").click().run()
            assert not app.exception, app.exception
            next(b for b in app.button if b.label == "Open Overview").click().run()
            assert not app.exception, app.exception
            assert len(app.metric) == 4
            assert writes == ["datasets/demo", "models/train", "forecasts", "recommendations"]
            app.radio(key="page").set_value("Model Performance").run()
            assert not app.exception, app.exception
            next(s for s in app.selectbox if s.label == "Evaluation horizon (days)").set_value(30).run()
            assert not app.exception, app.exception
            next(s for s in app.selectbox if s.label == "Evaluation context").set_value("validation").run()
            assert not app.exception, app.exception
            app.run()
            assert len(writes) == 4

def test_presentation_formats_and_api_only_boundary():
    import ast
    from app.ui.common import amount, compact_money, number, percent, line
    from app.ui.scenario import unapplied
    import plotly.graph_objects as go
    assert amount('1234.50') == 'PKR 1,234.50'
    assert compact_money('1234567.89') == 'PKR 1.23M'
    assert amount(None) == compact_money(None) == 'Unavailable'
    assert number(None) is None and number('0') == 0
    assert percent('0.205') == '20.5%'
    base = {'fx_change_pct': '0', 'supplier_price_change_pct': '0', 'additional_freight_pkr': '0',
            'extra_lead_days': 0, 'target_margin_override': None}
    assert not unapplied(base, {**base, 'fx_change_pct': '0.0000'})
    assert unapplied(base, {**base, 'target_margin_override': '0'})
    fig = go.Figure()
    line(fig, ['2026-10-16', '2026-10-17'], ['12.345', None], 'Stock')
    assert tuple(fig.data[0].y) == (12.345, None)
    for source in (PROJECT_ROOT / 'app/ui').glob('*.py'):
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            modules = [node.module or ''] if isinstance(node, ast.ImportFrom) else [n.name for n in node.names] if isinstance(node, ast.Import) else []
            assert not any(m.startswith(('app.services', 'app.db', 'app.ml', 'app.data', 'sqlalchemy', 'sqlite3')) for m in modules), source


def test_streamlit_unavailable_product_states(settings, db, batch_dir):
    from conftest import mutate_csv
    mutate_csv(batch_dir, 'supplier_quotes.csv', lambda rows: rows.clear())
    dataset_id, forecast_id = persisted_fixture(db, settings, batch_dir, insufficient=True)
    result = create_recommendations(db, settings, dataset_id, forecast_id)
    with TestClient(create_app(settings)) as backend:
        def request(self, method, path, **kwargs):
            assert method == 'GET', 'Rendering must not write data'
            response = backend.request(method, '/api/v1/' + path, **kwargs)
            assert response.is_success, response.text
            return response
        with patch.object(APIClient, 'request', request):
            app = app_test().run()
            assert not app.exception, app.exception
            assert any(m.value == 'Unavailable' for m in app.metric)
            next(b for b in app.button if b.label == 'Inspect product').click().run()
            app.selectbox(key='product').set_value(result['products'][0]['sku']).run()
            assert not app.exception, app.exception
            assert any('unavailable' in i.value.lower() for i in app.info)
            assert sum(m.value == 'Unavailable' for m in app.metric) == 2
