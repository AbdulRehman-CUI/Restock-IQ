"""Regression checks for saved-context resolution, narrow reads, and recovery."""
import csv
from datetime import date
import io
import zipfile
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import event
from app.api.main import create_app
from app.db.models import ModelRun, RecommendationRun
from app.ui.api_client import APIClient, APIError
from app.ui.common import saved_get, saved_download
from app.ui.state import demo_analysis, resolve_analysis, reset_dataset
from test_dashboard import analysis, app_test


def test_compact_reads_preserve_exact_saved_results(settings, analysis):
    run_id, forecast_id = analysis['recommendation_run_id'], analysis['forecast_run_id']
    with TestClient(create_app(settings)) as backend:
        root = '/api/v1/'
        statements = []
        event.listen(backend.app.state.db.engine, 'before_cursor_execute',
            lambda connection, cursor, statement, params, context, many: statements.append(statement))
        metadata = backend.get(root + f'recommendations/{run_id}/metadata').json()
        assert metadata == {k: v for k, v in analysis.items() if k != 'products'}
        assert not any('recommendation_products' in s for s in statements)
        statements.clear()
        backend.get(root + f'forecasts/{forecast_id}/metadata').raise_for_status()
        assert not any('forecast_rows' in s for s in statements)
        catalog = backend.get(root + f'recommendations/{run_id}/catalog').json()
        assert catalog == [{'sku': p['sku'], 'name': p['name']} for p in analysis['products']]
        from app.services.dashboard import overview
        assert backend.get(root + f'recommendations/{run_id}/overview').json() == overview(analysis['products'])
        workspace = backend.get(root + f'recommendations/{run_id}/workspace').json()
        assert workspace['summary'] == overview(analysis['products'])
        assert all('daily' not in p['inventory'] for p in workspace['products'])
        full_forecast = backend.get(root + f'forecasts/{forecast_id}').json()
        for p in analysis['products']:
            detail = backend.get(root + f'recommendations/{run_id}/products/{p["sku"]}').json()
            assert detail['product'] == p
            future = backend.get(root + f'forecasts/{forecast_id}/products/{p["sku"]}').json()
            assert future['rows'] == [r for r in full_forecast['rows'] if r['sku'] == p['sku']]
            assert future['row_count'] == len(future['rows'])
            assert len(future['product_status']) == 1 and future['product_status'][0]['sku'] == p['sku']
        assert backend.get(root + f'forecasts/{forecast_id}/products/unknown').status_code == 404
        assert backend.get(root + f'recommendations/{uuid4()}/overview').status_code == 404
        assert backend.get(root + f'recommendations/{run_id}').json() == analysis
        templates = backend.get(root + 'data-contracts/templates')
        from app.data.contracts import CONTRACTS
        with zipfile.ZipFile(io.BytesIO(templates.content)) as archive:
            for filename, contract in CONTRACTS.items():
                rows = list(csv.reader(io.StringIO(archive.read(filename).decode())))
                assert rows == [list(contract.model_fields)]
            assert 'not usable business history' in archive.read('README.txt').decode()


def test_catalog_resolves_and_rejects_incompatible_saved_dependencies(settings, analysis):
    with TestClient(create_app(settings)) as backend:
        invalid_id = str(uuid4())
        with backend.app.state.db.transaction() as session:
            source = session.get(RecommendationRun, analysis['recommendation_run_id'])
            values = {c.name: getattr(source, c.name) for c in RecommendationRun.__table__.columns}
            session.add(RecommendationRun(**{**values, 'id': invalid_id, 'horizon_days': 1}))
        rows = backend.get('/api/v1/analyses').json()
        assert [r['id'] for r in rows] == [analysis['recommendation_run_id']]
        # Saved forecasts from completed evaluation artifacts are supported by
        # the existing decision service too; discovery must not hide those runs.
        with backend.app.state.db.transaction() as session:
            session.get(ModelRun, analysis['model_id']).role = 'evaluation'
        assert backend.get('/api/v1/analyses').json() == rows
        state = {'dataset': 'other', 'model': 'other', 'product': 'stale', 'scenario_result': 'stale', 'report_ready': 'stale'}
        resolve_analysis(state, rows[0])
        assert state['dataset'] == analysis['dataset_id']
        assert state['model'] == analysis['model_id']
        assert state['forecast'] == analysis['forecast_run_id']
        assert 'product' not in state and 'scenario_result' not in state and 'report_ready' not in state
        assert demo_analysis([{**rows[0], 'provenance': 'uploaded'}]) is None
        assert demo_analysis([{**rows[0], 'provenance': 'synthetic'}])['id'] == rows[0]['id']


def test_immutable_cache_keys_and_fresh_mutable_reads():
    saved_get.clear()
    saved_download.clear()
    calls = []
    def get(self, path, **params):
        calls.append((self.base_url, path))
        return {'key': (self.base_url, path)}
    with patch.object(APIClient, 'get', get):
        paths = ['recommendations/run-a/products/sku-a', 'recommendations/run-b/products/sku-a',
                 'datasets/data-a/products/sku-a/sales?through=2026-10-15',
                 'datasets/data-b/products/sku-a/sales?through=2026-10-15',
                 'recommendations/run-a/products/sku-b']
        for path in paths:
            for base in ('http://first:8000', 'http://second:8000'):
                assert saved_get(base, path) == saved_get(base, path)
                assert saved_get(base, path)['key'] == (base, path)
        assert len(calls) == len(paths) * 2
        api = APIClient('http://first:8000')
        for _ in range(2):
            api.get('jobs/job-a')
            api.get('recommendations/run-a/actions')
        assert len(calls) == len(paths) * 2 + 4
    with patch.object(APIClient, 'download', autospec=True, side_effect=lambda self, path: (self.base_url + path).encode()) as read:
        for base, path in [('http://first:8000', 'run-a/export'), ('http://second:8000', 'run-a/export'), ('http://first:8000', 'run-b/export')]:
            assert saved_download(base, path) == saved_download(base, path) == (base + path).encode()
        assert read.call_count == 3


def test_demo_navigation_narrow_requests_and_reconnect(settings, analysis):
    with TestClient(create_app(settings)) as backend:
        calls, offline = [], [False]
        def request(self, method, path, **kwargs):
            calls.append((method, path))
            if offline[0]:
                raise APIError('Connection temporarily unavailable')
            response = backend.request(method, '/api/v1/' + path, **kwargs)
            assert response.is_success, response.text
            return response
        with patch.object(APIClient, 'request', request):
            app = app_test().run()
            next(b for b in app.button if b.label == 'Explore demo').click().run()
            # This fixture is uploaded; no synthetic result may be misrepresented as demo.
            assert app.radio(key='page').value == 'Data & Settings'
            assert app.selectbox(key='setup_step').value == 'Load data'
            assert app.radio(key='setup_source').value == 'Synthetic dataset'
            assert not any(method == 'POST' for method, path in calls)
            app.radio(key='page').set_value('Overview').run()
            calls.clear()
            app.run()
            assert calls == [('GET', 'datasets'), ('GET', 'analyses')]
            saved = {k: app.session_state[k] for k in ('dataset', 'run', 'model', 'forecast')}
            offline[0] = True
            app.run()
            assert not app.exception and app.error
            assert all(app.session_state[k] == v for k, v in saved.items())
            offline[0] = False
            next(b for b in app.button if b.label == 'Reconnect / refresh').click().run()
            assert not app.exception and len(app.metric) == 4
            calls.clear()
            app.radio(key='page').set_value('Model Performance').run()
            assert not app.exception
            assert not any('/products/' in p or '/export' in p or '/workspace' in p for _, p in calls)
            calls.clear()
            app.radio(key='page').set_value('Products').run()
            assert not app.exception
            assert sum('/sales?' in p for _, p in calls) == 1
            assert not any(p.endswith('/actions') for _, p in calls)
            calls.clear()
            next(r for r in app.radio if r.label == 'Product view').set_value('Pricing & Costs').run()
            assert calls == [('GET', 'datasets'), ('GET', 'analyses')]
            assert not any(method == 'POST' for method, path in calls)


def test_scenario_failure_retains_assumptions_and_retries_only_calculation(settings, analysis):
    with TestClient(create_app(settings)) as backend:
        writes, fail = [], [True]
        def request(self, method, path, **kwargs):
            if method == 'POST':
                writes.append(path)
                if path == 'recommendations' and fail[0]:
                    raise APIError('Calculation unavailable; retry the saved scenario.')
            response = backend.request(method, '/api/v1/' + path, **kwargs)
            assert response.is_success, response.text
            return response
        with patch.object(APIClient, 'request', request):
            app = app_test().run()
            app.radio(key='page').set_value('What-if Scenarios').run()
            app.selectbox(key='scenario_preset').set_value('Delivery delay only').run()
            assert app.session_state['scenario_fx'] == 0 and app.session_state['scenario_lead'] == 7
            assert not app.session_state['scenario_override']
            next(b for b in app.button if b.label == 'Run scenario').click().run()
            assert not app.exception and app.error
            saved_scenario = app.session_state['scenario_created']
            assert writes == ['scenarios', 'recommendations']
            app.run()
            assert writes == ['scenarios', 'recommendations']
            fail[0] = False
            next(b for b in app.button if b.label == 'Retry saved scenario calculation').click().run()
            assert not app.exception, app.exception
            assert writes == ['scenarios', 'recommendations', 'recommendations']
            assert app.session_state['saved_scenario'] == saved_scenario
            app.run()
            assert writes == ['scenarios', 'recommendations', 'recommendations']


def test_analysis_failure_reuses_completed_forecast(settings, analysis):
    with TestClient(create_app(settings)) as backend:
        writes, fail = [], [True]
        def request(self, method, path, **kwargs):
            if method == 'POST':
                writes.append(path)
                if path == 'recommendations' and fail[0]:
                    raise APIError('Could not save recommendations')
            response = backend.request(method, '/api/v1/' + path, **kwargs)
            assert response.is_success, response.text
            return response
        candidate = {'training_cutoff': date.fromisoformat(analysis['origin']), 'trained_skus': [], 'pipeline': None}
        with patch.object(APIClient, 'request', request), patch('app.services.forecasting.load_artifact', return_value=candidate):
            app = app_test().run()
            app.radio(key='page').set_value('Data & Settings').run()
            app.selectbox(key='setup_step').set_value('Generate analysis').run()
            next(n for n in app.number_input if n.label == 'Outlook (days)').set_value(29)
            next(b for b in app.button if b.label == 'Generate analysis').click().run()
            assert not app.exception and app.error
            forecast_id = app.session_state['pending_forecast']
            app.session_state['scenario_result'] = 'old-forecast-result'
            app.session_state['report_ready'] = ('http://127.0.0.1:8000', 'old-forecast-export')
            app.run()
            assert app.session_state['forecast'] == forecast_id
            assert 'scenario_result' not in app.session_state and 'report_ready' not in app.session_state
            assert writes == ['forecasts', 'recommendations']
            fail[0] = False
            next(b for b in app.button if b.label == 'Generate analysis').click().run()
            assert not app.exception, app.exception
            assert writes == ['forecasts', 'recommendations', 'recommendations']
            result = backend.get('/api/v1/recommendations/' + app.session_state['run']).json()
            assert result['forecast_run_id'] == forecast_id and result['horizon_days'] == 29
            app.selectbox(key='setup_step').set_value('Generate analysis').run()
            next(b for b in app.button if b.label == 'Generate analysis').click().run()
            assert writes == ['forecasts', 'recommendations', 'recommendations']


def test_terminal_job_stops_polling():
    from streamlit.testing.v1 import AppTest
    script = '''
import streamlit as st
from app.ui.api_client import APIClient
from app.ui.data_settings import render
if not st.session_state.get('initialized'):
    st.session_state.update(initialized=True, job='tracked-job', setup_step='Review validation')
render(APIClient(), {})
'''
    job = {'id': 'tracked-job', 'status': 'failed', 'stage': 'interrupted', 'error': 'Retry training', 'dataset_id': None}
    with patch.object(APIClient, 'get', return_value=job) as calls:
        app = AppTest.from_string(script).run()
        assert not app.exception, app.exception
        assert 'job' not in app.session_state and app.session_state['last_job'] == job
        app.run()
        assert calls.call_count == 1


def test_explore_demo_reuses_saved_base_without_writes(db, settings, batch_dir):
    from app.services.ingestion import import_dataset
    from app.services.recommendations import create_recommendations
    from test_scenarios import persisted_fixture
    # This batch came from the test synthetic generator; use its trusted internal import path.
    with patch('test_scenarios.import_dataset', side_effect=lambda db, settings, path: import_dataset(db, settings, path, synthetic=True)):
        ds, forecast = persisted_fixture(db, settings, batch_dir)
    result = create_recommendations(db, settings, ds, forecast)
    with TestClient(create_app(settings)) as backend:
        def request(self, method, path, **kwargs):
            assert method == 'GET', 'Explore demo must reuse a compatible saved analysis'
            response = backend.request(method, '/api/v1/' + path, **kwargs)
            assert response.is_success, response.text
            return response
        with patch.object(APIClient, 'request', request):
            app = app_test().run()
            next(b for b in app.button if b.label == 'Explore demo').click().run()
            assert not app.exception, app.exception
            assert app.session_state['run'] == result['recommendation_run_id']
            assert app.session_state['forecast'] == forecast
            assert app.session_state['model'] == result['model_id']
            assert any('Synthetic data' in m.value for m in app.markdown)
