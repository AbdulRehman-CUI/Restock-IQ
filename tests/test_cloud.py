from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from app.api.main import create_app
from app.core.config import Settings
from app.db.models import Job
from app.services.ingestion import utc_now

@pytest.fixture
def cloud(tmp_path, monkeypatch):
    monkeypatch.delenv('RESTOCKIQ_ADMIN_PASSWORD', raising=False)
    settings = Settings(data_dir=tmp_path/'data', runtime_dir=tmp_path/'run', artifact_dir=tmp_path/'artifacts', cloud_mode=True)
    with TestClient(create_app(settings)) as client:
        yield client

def test_public_api_and_origin(cloud):
    home = cloud.get('/', follow_redirects=False)
    assert home.status_code == 200
    assert '/app.js' in home.text
    assert 'set-cookie' not in home.headers
    for method, path in (('POST', 'login'), ('POST', 'logout'), ('GET', 'session')):
        assert cloud.request(method, f'/api/v1/{path}').status_code in (404, 405)
    for path in ('datasets', 'analyses', 'health'):
        response = cloud.get(f'/api/v1/{path}', follow_redirects=False)
        assert response.status_code == 200
        assert response.headers['Cache-Control'] == 'no-store'
    assert cloud.post('/api/v1/datasets/demo', headers={'Origin':'https://other.example'}).status_code == 403

def test_bulk_imports_unavailable_in_public_demo(cloud):
    identifier = uuid4()
    paths = (
        ('POST', 'datasets/import'),
        ('POST', 'uploads'),
        ('PUT', f'uploads/{identifier}/products.csv/0'),
        ('POST', f'uploads/{identifier}/complete'),
        ('POST', f'market/datasets/{identifier}/quote-imports'),
        ('PUT', f'market/quote-imports/{identifier}/chunks/0'),
        ('POST', f'market/quote-imports/{identifier}/preview'),
        ('POST', f'market/quote-imports/{identifier}/confirm'),
    )
    for method, path in paths:
        assert cloud.request(method, f'/api/v1/{path}').status_code in (404, 405)
    assert cloud.get('/api/v1/datasets').json() == []

def test_job_waits_for_execution_and_stale_recovery(cloud):
    result = cloud.post('/api/v1/datasets/demo', json={'seed':42,'start_date':'2025-01-01','days':40,'sku_count':2})
    job_id = result.json()['job_id']
    assert cloud.get(f'/api/v1/jobs/{job_id}').json()['status'] == 'queued'
    result = cloud.post(f'/api/v1/jobs/{job_id}/execute')
    assert result.json()['status'] == 'completed', result.text
    assert cloud.post(f'/api/v1/jobs/{job_id}/execute').json()['dataset_id'] == result.json()['dataset_id']
    assert len(cloud.get('/api/v1/datasets').json()) == 1
    identifier=str(uuid4())
    with cloud.app.state.db.transaction() as session:
        session.add(Job(id=identifier,status='running',stage='test',progress=20,active_key='heavy',
            created_at=utc_now(),updated_at=(datetime.now(timezone.utc)-timedelta(minutes=11)).isoformat(),configuration={}))
    assert cloud.get(f'/api/v1/jobs/{identifier}').json()['status'] == 'failed'
