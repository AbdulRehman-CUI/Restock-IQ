from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from app.api.main import create_app
from app.core.config import Settings
from app.db.models import Job
from app.services.ingestion import utc_now

@pytest.fixture
def cloud(tmp_path, monkeypatch):
    monkeypatch.setenv('RESTOCKIQ_ADMIN_PASSWORD', 'test-workspace-password')
    settings = Settings(data_dir=tmp_path/'data', runtime_dir=tmp_path/'run', artifact_dir=tmp_path/'artifacts', cloud_mode=True)
    with TestClient(create_app(settings)) as client:
        yield client

def signin(client):
    assert client.post('/api/v1/login', json={'password':'test-workspace-password'}).status_code == 200

def test_private_api_and_origin(cloud):
    assert cloud.get('/').status_code == 200
    assert cloud.get('/api/v1/datasets').status_code == 401
    assert cloud.post('/api/v1/login', json={'password':'wrong'}).status_code == 401
    signin(cloud)
    assert cloud.get('/api/v1/datasets').status_code == 200
    assert cloud.post('/api/v1/uploads', headers={'Origin':'https://other.example'}).status_code == 403
    assert cloud.post('/api/v1/logout').status_code == 200
    assert cloud.get('/api/v1/datasets').status_code == 401

def test_chunked_import_atomic_and_idempotent(cloud):
    signin(cloud)
    batch = cloud.post('/api/v1/uploads').json()['id']
    root = Path(__file__).parent/'fixtures'/'small'
    sizes = {}
    for path in sorted(root.glob('*.csv')):
        data = path.read_bytes(); sizes[path.name] = len(data)
        url = f'/api/v1/uploads/{batch}/{path.name}/0'
        assert cloud.put(url, content=data).status_code == 200
        assert cloud.put(url, content=data).status_code == 200
        assert cloud.put(url, content=b'different').status_code == 409
    bad = dict(sizes); bad['products.csv'] += 1
    assert cloud.post(f'/api/v1/uploads/{batch}/complete', json={'sizes':bad}).status_code == 422
    assert cloud.get('/api/v1/datasets').json() == []
    response = cloud.post(f'/api/v1/uploads/{batch}/complete', json={'sizes':sizes})
    assert response.status_code == 201, response.text
    assert response.json()['dataset_id'] == batch
    assert cloud.post(f'/api/v1/uploads/{batch}/complete', json={'sizes':sizes}).json()['dataset_id'] == batch
    assert len(cloud.get('/api/v1/datasets').json()) == 1

def test_job_waits_for_execution_and_stale_recovery(cloud):
    signin(cloud)
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
