"""Optional real PostgreSQL verification in a disposable LOCAL schema only.

Set RESTOCKIQ_TEST_POSTGRES_URL to a localhost test server. Never use DATABASE_URL.
No production credentials, tables or schemas are read or changed.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, inspect, func, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DatabaseError
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.models import Base, Dataset
from app.db.market import MARKET_TABLES, SchemaVersion, MarketQuote, FXObservation
from app.db.session import Database
from app.services.fx import refresh_fx
from app.services.market import create_assessment, create_quote
from app.services.ingestion import import_dataset
from app.services import quote_imports
from app.api.schemas.market import ConfirmImport
from test_market import RecordedProvider, NOW, quote, upload, quote_csv

URL = os.environ.get("RESTOCKIQ_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="Requires an isolated localhost PostgreSQL test server")


@pytest.fixture
def postgres(settings, tmp_path):
    url = make_url(URL)
    if url.host not in ("127.0.0.1", "localhost", "::1"):
        pytest.fail("PostgreSQL migration tests require a localhost test server")
    schema = "restockiq_test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    isolated = url.update_query_dict({"options": "-csearch_path=" + schema})
    db = Database(tmp_path/"unused.sqlite3", isolated.render_as_string(hide_password=False))
    try:
        yield db
    finally:
        db.engine.dispose()
        # Generated schema only; this test never accepts a caller-provided schema name.
        assert schema.startswith("restockiq_test_") and len(schema) == len("restockiq_test_") + 32
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def test_postgres_additive_migration_and_current_pricing(postgres, settings):
    db = postgres
    new = {table.name for table in MARKET_TABLES} | {"schema_versions"}
    with db.engine.begin() as connection:
        Base.metadata.create_all(connection, tables=[table for table in Base.metadata.sorted_tables if table.name not in new])
    dataset = import_dataset(db,settings,Path(__file__).parent/"fixtures"/"small")["dataset_id"]
    before = set(inspect(db.engine).get_table_names())
    db.initialize(); db.initialize()
    assert before.issubset(set(inspect(db.engine).get_table_names()))
    with db.sessions() as session:
        assert session.get(Dataset,dataset)
        assert list(session.scalars(select(SchemaVersion.version))) == [1]
    create_quote(db,dataset,quote(),"postgres-test")
    refresh_fx(db,settings,RecordedProvider(),NOW)
    current=create_assessment(db,settings,dataset,"CHG-001",NOW)
    assert current["products"][0]["replacement_cost_pkr"] == "3000.00"
    with pytest.raises(DatabaseError),db.transaction() as session:
        session.execute(update(FXObservation).values(source="cannot mutate provenance"))
    with pytest.raises(DatabaseError),db.transaction() as session:
        session.execute(update(MarketQuote).values(supplied_by="cannot mutate confirmation"))


def test_postgres_concurrent_refresh_import_confirmation(postgres, settings):
    db=postgres;db.initialize()
    dataset=import_dataset(db,settings,Path(__file__).parent/"fixtures"/"small")["dataset_id"]
    provider=RecordedProvider()
    with ThreadPoolExecutor(4) as pool:
        results=list(pool.map(lambda _: refresh_fx(db,settings,provider,NOW),range(4)))
    assert provider.calls==1
    identifier,preview=upload(db,dataset,quote_csv([quote().model_dump(mode="json")]))
    body=ConfirmImport(confirm=True,preview_hash=preview["preview_hash"])
    with ThreadPoolExecutor(4) as pool:
        results=list(pool.map(lambda _: quote_imports.confirm_upload(db,identifier,body,"postgres-test"),range(4)))
    assert all(result==results[0] for result in results)
    with db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(MarketQuote))==1
