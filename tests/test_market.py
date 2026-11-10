"""Recorded FX and isolated databases: no live provider or browser automation."""
import csv
import io
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from threading import Event
from uuid import uuid4
from zipfile import ZipFile

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, func, update, inspect, text
from sqlalchemy.exc import DatabaseError

from app.api.main import create_app
from app.api.schemas.market import QuoteInput, RateSettings, ReconfirmQuote, UploadStart, ConfirmImport
from app.core.errors import StateConflict
from app.db.market import FXObservation, MarketQuote, PricingAssessment, SchemaVersion
from app.db.models import Dataset, Product, FXRate, SupplierQuote, Base
from app.db.session import Database
from app.services.fx import (ExchangeRateAPI, ProviderError, Observation, parse_observation, refresh_fx, fx_status, instant)
from app.services.ingestion import import_dataset
from app.services.market import (create_quote, create_assessment, save_settings, reconfirm_quote, quote_history,
    compare_assessments, auto_assess, product_result)
from app.services import quote_imports
from app.services.recommendations import create_recommendations, get_recommendations
from app.services.pricing import PricingSettings
from test_scenarios import persisted_fixture

NOW = datetime.now(timezone.utc).replace(microsecond=0)
OBSERVED = NOW - timedelta(hours=2)


def response_data(**changes):
    data = {"result": "success", "base_code": "USD", "rates": {"USD": 1, "PKR": 280.1234567},
            "time_last_update_unix": int(OBSERVED.timestamp()),
            "time_next_update_unix": int((OBSERVED + timedelta(days=1)).timestamp())}
    return dict(data, **changes)


class RecordedProvider:
    def __init__(self, rate="280", observed=OBSERVED):
        self.calls = 0
        self.observation = Observation(D(rate), observed, observed + timedelta(days=1))

    def fetch(self, now):
        self.calls += 1
        return self.observation


@pytest.fixture
def dataset(db, settings):
    return import_dataset(db, settings, Path(__file__).parent / "fixtures" / "small")["dataset_id"]


def quote(**changes):
    return QuoteInput.model_validate(dict(sku="CHG-001", supplier_id="SUP-01", currency="USD", unit_price="10.00",
        freight_unit_pkr="200.00", other_unit_pkr="0.00", observed_at=(NOW-timedelta(days=1)).isoformat(),
        valid_until=(NOW+timedelta(days=10)).isoformat(), **changes))


def modified_quote(**changes):
    return QuoteInput.model_validate(dict(quote().model_dump(), **changes))


def test_provider_decimal_success_and_fixed_url():
    calls = []
    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=json.dumps(response_data()))
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = ExchangeRateAPI(client).fetch(NOW)
    assert result.rate == D("280.123457")
    assert result.observed_at == OBSERVED and result.next_update_at > result.observed_at
    assert calls == ["https://open.er-api.com/v6/latest/USD"]


@pytest.mark.parametrize("payload", [b"bad json", b"null", b"[]", json.dumps(response_data(result="error")).encode(),
    json.dumps(response_data(base_code="EUR")).encode(), json.dumps(response_data(rates={"USD":1})).encode(),
    json.dumps(response_data(rates={"USD":1,"PKR":0})).encode(), json.dumps(response_data(rates={"USD":1,"PKR":"NaN"})).encode(),
    json.dumps(response_data(rates={"USD":1,"PKR":True})).encode(),
    json.dumps(response_data(time_next_update_unix=int((OBSERVED-timedelta(hours=1)).timestamp()))).encode(),
    json.dumps(response_data(time_last_update_unix=int((NOW+timedelta(days=1)).timestamp()))).encode(),
    json.dumps(response_data(time_last_update_unix=True)).encode(),
    json.dumps(response_data(time_last_update_utc="Fri, 02 Apr 2020 00:00:00 +0000")).encode()])
def test_provider_rejects_malformed(payload):
    with pytest.raises(ProviderError):
        parse_observation(payload, NOW)


def test_provider_timeouts_retry_after_and_no_redirects():
    attempts, sleeps = [], []
    def timeout(request):
        attempts.append(request)
        raise httpx.ReadTimeout("recorded timeout")
    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(ProviderError):
            ExchangeRateAPI(client, sleep=sleeps.append).fetch(NOW)
    assert len(attempts) == 3 and sleeps == [0.25, 0.5]
    for status, headers in [(429, {"Retry-After":"3600"}), (503, {"Retry-After":"3600"}), (302, {"Location":"http://untrusted.test"})]:
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, headers=headers))) as client:
            with pytest.raises(ProviderError) as error:
                ExchangeRateAPI(client, sleep=sleeps.append).fetch(NOW)
        if status != 302:
            assert error.value.retry_at == NOW + timedelta(hours=1)


def test_persistent_cache_duplicate_old_observation_and_failure(db, settings):
    provider = RecordedProvider()
    first = refresh_fx(db, settings, provider, NOW)
    assert provider.calls == 1
    assert first["observation"]["observed_at"] != first["observation"]["fetched_at"]
    assert first["last_success_at"] == instant(NOW)
    refresh_fx(db, settings, provider, NOW+timedelta(hours=1))
    assert provider.calls == 1
    later = refresh_fx(db, settings, provider, NOW+timedelta(days=5))
    assert later["status"] == "stale" and later["observation"] == first["observation"]
    assert later["age_seconds"] > first["age_seconds"]
    with db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(FXObservation)) == 1
    class Broken:
        def fetch(self, now):
            raise ProviderError("recorded failure")
    failed = refresh_fx(db, settings, Broken(), NOW+timedelta(days=7))
    assert failed["refresh_failed"] and failed["status"] == "stale"
    assert failed["observation"] == first["observation"]
    second_engine = Database(settings.runtime_dir/"restockiq.sqlite3")
    assert fx_status(second_engine, settings, NOW+timedelta(days=7))["observation"] == first["observation"]
    second_engine.engine.dispose()


def test_concurrent_refresh_uses_database_lease(db, settings):
    entered, release = Event(), Event()
    class Slow(RecordedProvider):
        def fetch(self, now):
            entered.set()
            assert release.wait(10)
            return super().fetch(now)
    provider = Slow()
    other = Database(settings.runtime_dir/"restockiq.sqlite3")
    with ThreadPoolExecutor(2) as pool:
        task = pool.submit(refresh_fx, db, settings, provider, NOW)
        assert entered.wait(10)
        competing = refresh_fx(other, settings, provider, NOW)
        assert competing["status"] == "unavailable"
        release.set()
        assert task.result()["status"] == "fresh"
    assert provider.calls == 1
    other.engine.dispose()


def test_unavailable_and_no_synthetic_fallback(db, settings, dataset):
    create_quote(db, dataset, quote(), "test")
    result = create_assessment(db, settings, dataset, "CHG-001", NOW)
    product = result["products"][0]
    assert result["fx"]["status"] == "unavailable"
    assert product["status"] == "unavailable" and product["replacement_cost_pkr"] is None
    assert "MISSING_FX_RATE" in product["warnings"]


def test_full_fx_workflow_preserves_historical_records(db, settings, batch_dir):
    dataset_id, forecast_id = persisted_fixture(db, settings, batch_dir)
    historical = create_recommendations(db, settings, dataset_id, forecast_id)
    before_quote_count = None
    with db.sessions() as session:
        before_quote_count = session.scalar(select(func.count()).select_from(SupplierQuote))
        source_rates = [(r.dataset_id,r.date,r.currency,r.pkr_per_unit) for r in session.scalars(select(FXRate))]
    create_quote(db, dataset_id, quote(), "test")
    save_settings(db, dataset_id, RateSettings(target_margin="0.20", pricing=PricingSettings(price_step_pkr="0.01")), "test")
    refresh_fx(db, settings, RecordedProvider("280"), NOW)
    first = create_assessment(db, settings, dataset_id, "CHG-001", NOW)
    later = NOW+timedelta(days=1, seconds=1)
    refresh_fx(db, settings, RecordedProvider("310", later-timedelta(hours=1)), later)
    second = create_assessment(db, settings, dataset_id, "CHG-001", later)
    a,b = first["products"][0],second["products"][0]
    assert D(a["replacement_cost_pkr"]) == 3000 and D(b["replacement_cost_pkr"]) == 3300
    assert D(a["pricing"]["suggested_price_pkr"]) == 3750 and D(b["pricing"]["suggested_price_pkr"]) == 4125
    assert D(b["pricing"]["suggested_price_pkr"])-D(a["pricing"]["suggested_price_pkr"]) == 375
    assert "MATERIAL_FX_PRICE_CHANGE" in b["warnings"]
    assert create_assessment(db, settings, dataset_id, "CHG-001", later)["id"] == second["id"]
    comparison = compare_assessments(db, first["id"], second["id"])["products"][0]
    assert comparison["attribution"] == "FX only" and comparison["alerts"] == ["MATERIAL_FX_PRICE_CHANGE"]
    assert "FX changed; the supplier’s USD price is assumed unchanged since" in comparison["explanation"]
    assert get_recommendations(db, historical["recommendation_run_id"]) == historical
    with db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(SupplierQuote)) == before_quote_count
        assert [(r.dataset_id,r.date,r.currency,r.pkr_per_unit) for r in session.scalars(select(FXRate))] == source_rates
    assert second["context"]["dataset_origin"] != second["assessed_at"][:10]


def test_spread_manual_pkr_and_fee_precision(db, settings, dataset):
    create_quote(db, dataset, quote(), "test")
    refresh_fx(db, settings, RecordedProvider("280"), NOW)
    save_settings(db, dataset, RateSettings(spread_percent="2"), "test")
    spread = create_assessment(db, settings, dataset, "CHG-001", NOW)["products"][0]
    assert D(spread["effective_rate"]) == D("285.6") and D(spread["replacement_cost_pkr"]) == 3056
    manual = RateSettings(mode="manual_bank", manual_rate="310", manual_source="bank counter",
        manual_observed_at=OBSERVED, manual_valid_until=NOW+timedelta(days=2), order_quantity=6, payment_fee_total_pkr="1", payment_fee_sku="CHG-001")
    save_settings(db, dataset, manual, "test")
    result = create_assessment(db, settings, dataset, "CHG-001", NOW)["products"][0]
    assert D(result["effective_rate"]) == 310
    assert result["unrounded_replacement_cost_pkr"].startswith("3300.166666")
    assert D(result["minimum_replacement_funding_price_pkr"]) == D("3300.17")
    create_quote(db, dataset, modified_quote(currency="PKR", unit_price="100"), "test")
    local = create_assessment(db, settings, dataset, "CHG-001", NOW)["products"][0]
    assert D(local["effective_rate"]) == 1 and D(local["replacement_cost_pkr"]) == D("300.17")
    assert not any("FX" in warning for warning in local["warnings"])


@pytest.mark.parametrize("changes", [{"spread_percent":"2", "mode":"manual_bank", "manual_rate":"310", "manual_source":"bank", "manual_observed_at":OBSERVED},
    {"payment_fee_total_pkr":"100"}, {"manual_rate":"300"}, {"mode":"manual_bank"}, {"target_margin":"20"},
    {"manual_rate":"NaN"}, {"order_quantity":1.2}])
def test_rate_settings_validation(changes):
    with pytest.raises(ValidationError):
        RateSettings(**changes)


def test_quote_versions_expiry_units_costs_and_caps(db, settings, dataset):
    bad = modified_quote(supplier_id="unrelated")
    with pytest.raises(StateConflict):
        create_quote(db, dataset, bad, "test")
    with pytest.raises(StateConflict):
        create_quote(db, dataset, modified_quote(unit_basis="pack",pack_units=3), "test")
    pack = create_quote(db, dataset, modified_quote(unit_basis="pack",pack_units=2,unit_price="20"), "test", str(uuid4()))
    renewed = reconfirm_quote(db, dataset, pack["id"], ReconfirmQuote(observed_at=OBSERVED, note="Supplier confirmed unchanged"), "test")
    assert renewed["id"] != pack["id"] and renewed["reconfirms_id"] == pack["id"]
    assert len(quote_history(db,dataset)) == 2 and renewed["values"]["valid_until"] is None
    refresh_fx(db, settings, RecordedProvider(), NOW)
    save_settings(db,dataset,RateSettings(pricing=PricingSettings(max_price_change_pct="0")),"test")
    current = create_assessment(db,settings,dataset,"CHG-001",NOW)["products"][0]
    assert D(current["replacement_cost_pkr"]) == 3000
    assert "QUOTE_VALIDITY_UNKNOWN" in current["warnings"]
    assert "PRICE_CAP_PREVENTS_REPLACEMENT" in current["warnings"]
    assert D(current["pricing"]["funding_shortfall_unit_pkr"]) > 0
    assert not current["funding_assurance"]
    create_quote(db,dataset,modified_quote(observed_at=NOW-timedelta(days=40),valid_until=NOW-timedelta(days=1)),"test")
    # A backdated older version cannot displace a more recent confirmation.
    assert create_assessment(db,settings,dataset,"CHG-001",NOW)["products"][0]["quote"]["id"] == renewed["id"]
    create_quote(db,dataset,modified_quote(observed_at=NOW-timedelta(minutes=1),freight_unit_pkr=None),"test")
    missing = create_assessment(db,settings,dataset,"CHG-001",NOW)["products"][0]
    assert missing["status"] == "unavailable" and "MISSING_ACQUISITION_COSTS" in missing["warnings"]


def test_quantity_expiry_gross_margin_and_immutable_snapshots(db,settings,dataset):
    create_quote(db,dataset,modified_quote(min_quantity=10,max_quantity=20),"test")
    refresh_fx(db,settings,RecordedProvider(),NOW)
    result = create_assessment(db,settings,dataset,"CHG-001",NOW)
    assert "QUOTE_QUANTITY_MISMATCH" in result["products"][0]["warnings"]
    assert create_assessment(db,settings,dataset,"CHG-001",NOW)["id"] == result["id"]
    save_settings(db,dataset,RateSettings(order_quantity=12,target_margin="0.20",auto_assess=True),"test")
    good = create_assessment(db,settings,dataset,"CHG-001",NOW)
    price = good["products"][0]["pricing"]
    assert D(price["unrounded_target_price_pkr"]) == D("3750")
    assert D(price["achievable_margin"]) == D("0.20")  # 25% markup yields 20% gross margin.
    assert auto_assess(db,settings)
    with pytest.raises(DatabaseError),db.transaction() as session:
        session.execute(update(PricingAssessment).where(PricingAssessment.id==good["id"]).values(fingerprint="changed"))


def quote_csv(records):
    out=io.StringIO(newline="")
    writer=csv.DictWriter(out,quote_imports.FIELDS,lineterminator="\n")
    writer.writeheader()
    for record in records:
        writer.writerow({k:record.get(k) for k in quote_imports.FIELDS})
    return out.getvalue().encode()


def upload(db,dataset,payload,filename="quotes.csv"):
    value=quote_imports.start_upload(db,dataset,UploadStart(filename=filename,size=len(payload)),12*1024*1024)
    for i,offset in enumerate(range(0,len(payload),quote_imports.CHUNK_BYTES)):
        part=payload[offset:offset+quote_imports.CHUNK_BYTES]
        quote_imports.put_chunk(db,value["id"],i,part)
        quote_imports.put_chunk(db,value["id"],i,part)
    return value["id"],quote_imports.preview_upload(db,value["id"])


def test_import_preview_atomic_idempotency_and_invalid_rows(db,settings,dataset):
    q=quote().model_dump(mode="json")
    payload=quote_csv([q,dict(q,sku="unknown")])
    identifier,preview=upload(db,dataset,payload)
    assert preview["error_count"]==1 and not preview["can_confirm"]
    with pytest.raises(StateConflict):
        quote_imports.confirm_upload(db,identifier,ConfirmImport(preview_hash=preview["preview_hash"],confirm=True),"test")
    assert not quote_history(db,dataset)
    duplicate,preview=upload(db,dataset,quote_csv([q,q]))
    assert not preview["can_confirm"]
    identifier,preview=upload(db,dataset,quote_csv([q]))
    assert preview["can_confirm"] and not quote_history(db,dataset)
    confirmation=ConfirmImport(preview_hash=preview["preview_hash"],confirm=True)
    first=quote_imports.confirm_upload(db,identifier,confirmation,"test")
    second=quote_imports.confirm_upload(db,identifier,confirmation,"test")
    assert first==second and len(quote_history(db,dataset))==1


def xlsx_payload(rows,extra=None):
    from xml.sax.saxutils import escape
    out=io.BytesIO()
    with ZipFile(out,"w") as book:
        book.writestr("[Content_Types].xml",'<Types/>')
        cells=[]
        for i,row in enumerate(rows,1):
            values="".join(f'<c r="{chr(65+j)}{i}" t="inlineStr"><is><t>{escape(str(v))}</t></is></c>' for j,v in enumerate(row))
            cells.append(f'<row r="{i}">{values}</row>')
        book.writestr("xl/worksheets/sheet1.xml",'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+"".join(cells)+"</sheetData></worksheet>")
        if extra:
            book.writestr(*extra)
    return out.getvalue()


def test_xlsx_literal_values_and_safety_limits(db,settings,dataset):
    q=quote().model_dump(mode="json")
    rows=[quote_imports.FIELDS,[q[k] if q[k] is not None else "" for k in quote_imports.FIELDS]]
    identifier,preview=upload(db,dataset,xlsx_payload(rows),"quotes.xlsx")
    assert preview["can_confirm"],preview
    assert preview["quotes"][0]["source_type"]=="XLSX"
    for extra in [("xl/vbaProject.bin",b"macro"),("xl/externalLinks/externalLink1.xml",b"external"),
                  ("xl/sharedStrings.xml",b'<!DOCTYPE x [<!ENTITY y "expanded">]><sst/>'),
                  ("xl/worksheets/sheet2.xml",b'<worksheet/>'),
                  ("xl/sharedStrings.xml",b"x"*(quote_imports.MAX_EXPANSION+1))]:
        _,bad=upload(db,dataset,xlsx_payload(rows,extra),"quotes.xlsx")
        assert not bad["can_confirm"]
    with pytest.raises(StateConflict):
        quote_imports.start_upload(db,dataset,UploadStart(filename="quotes.xlsm",size=10),12*1024*1024)
    _,bad=upload(db,dataset,quote_csv([q]*1001))
    assert not bad["can_confirm"]


def test_migration_additive_preserves_existing_database(tmp_path):
    db=Database(tmp_path/"legacy.sqlite3")
    from app.db.market import MARKET_TABLES
    names={t.name for t in MARKET_TABLES}|{"schema_versions"}
    with db.engine.begin() as connection:
        Base.metadata.create_all(connection,tables=[t for t in Base.metadata.sorted_tables if t.name not in names])
    with db.transaction() as session:
        session.add(Dataset(id="legacy",provenance="uploaded",start_date=NOW.date(),end_date=NOW.date(),manifest={"keep":True},quality={}))
    db.initialize();db.initialize()
    with db.sessions() as session:
        assert session.get(Dataset,"legacy").manifest=={"keep":True}
        assert list(session.scalars(select(SchemaVersion.version)))==[1]
    assert "current_pricing_assessments" in inspect(db.engine).get_table_names()
    db.engine.dispose()


def test_synthetic_requires_explicit_real_fx_opt_in(db,settings,batch_dir):
    dataset=import_dataset(db,settings,batch_dir,synthetic=True)["dataset_id"]
    refresh_fx(db,settings,RecordedProvider("999"),NOW)
    original=create_assessment(db,settings,dataset,"CHG-001",NOW)
    assert original["label"]=="Entirely synthetic pricing" and original["fx"]["observation"] is None
    save_settings(db,dataset,RateSettings(real_fx_opt_in=True),"test")
    mixed=create_assessment(db,settings,dataset,"CHG-001",NOW)
    assert mixed["label"]=="Synthetic products with real FX"
    assert "UNCONFIRMED_QUOTE" in mixed["products"][0]["warnings"]
    assert D(mixed["products"][0]["effective_rate"])==999


def test_authenticated_api_schedule_export_and_dom(settings,monkeypatch):
    monkeypatch.setenv("RESTOCKIQ_ADMIN_PASSWORD","test-secret")
    monkeypatch.setenv("CRON_SECRET","recorded-cron-secret-only")
    with TestClient(create_app(replace(settings,cloud_mode=True))) as client:
        assert client.get("/api/v1/market/fx").status_code==401
        assert client.post("/api/v1/market/fx/refresh").status_code==401
        assert client.get("/api/v1/market/scheduled-refresh").status_code==401
        assert client.get("/api/v1/market/scheduled-refresh",headers={"Authorization":"Bearer wrong"}).status_code==401
        dataset=import_dataset(client.app.state.db,settings,Path(__file__).parent/"fixtures"/"small")["dataset_id"]
        root=f"/api/v1/market/datasets/{dataset}"
        client.post("/api/v1/login",json={"password":"test-secret"}).raise_for_status()
        key=str(uuid4())
        created=client.post(root+"/quotes",json=quote().model_dump(mode="json"),headers={"Idempotency-Key":key})
        assert created.status_code==201,created.text
        assert client.post(root+"/quotes",json=quote().model_dump(mode="json"),headers={"Idempotency-Key":key}).json()["id"]==created.json()["id"]
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr("app.api.routes.market.refresh_fx",lambda db,settings: refresh_fx(db,settings,RecordedProvider(),NOW))
            assert client.post("/api/v1/market/fx/refresh").status_code==200
            scheduled=client.get("/api/v1/market/scheduled-refresh",headers={"Authorization":"Bearer recorded-cron-secret-only"})
            assert scheduled.status_code==200 and "observation" not in scheduled.json()
        response=client.post(root+"/assessments",json={"sku":"CHG-001"})
        assert response.status_code==201,response.text
        value=response.json()
        exported=client.get(f"/api/v1/market/assessments/{value['id']}/export").json()
        assert exported==value
        exported_csv=client.get(f"/api/v1/market/assessments/{value['id']}/export?format=csv")
        assert "quote_json" in exported_csv.text and "280.000000" in exported_csv.text
        assert client.get("/market.js").status_code==200
        html=client.get("/").text
        assert '/market.js' in html
    source=(Path(__file__).parents[1]/"app/web/market.js").read_text(encoding="utf-8")
    assert "Recalculate current pricing" in source and "aria-live" in source
    assert 'timeZone: "Asia/Karachi"' in source and "Idempotency-Key" in source


def test_expired_stale_quotes_and_manual_rate_availability(db,settings,dataset):
    create_quote(db,dataset,modified_quote(observed_at=NOW-timedelta(days=40),valid_until=NOW-timedelta(days=1)),"test")
    save_settings(db,dataset,RateSettings(mode="manual_bank",manual_rate="310",manual_observed_at=OBSERVED,
                  manual_source="Bank sells USD",manual_valid_until=NOW+timedelta(days=1)),"test")
    result=create_assessment(db,settings,dataset,"CHG-001",NOW)
    p=result["products"][0]
    assert {"EXPIRED_QUOTE","STALE_QUOTE"}.issubset(p["warnings"])
    assert p["status"]=="indicative" and p["replacement_cost_pkr"]=="3300.00"
    assert result["rate_selection"]["fx_observation_id"] is None
    assert result["rate_selection"]["manual_supplied_by"]=="test"
    expired=create_assessment(db,settings,dataset,"CHG-001",NOW+timedelta(days=2))["products"][0]
    assert expired["status"]=="unavailable" and "MANUAL_RATE_EXPIRED" in expired["warnings"]


def test_import_rolls_back_after_first_insert_and_concurrent_retry(db,settings,dataset,monkeypatch):
    q=quote().model_dump(mode="json")
    second=dict(q,sku="CBL-002",supplier_id="SUP-02")
    identifier,preview=upload(db,dataset,quote_csv([q,second]))
    body=ConfirmImport(confirm=True,preview_hash=preview["preview_hash"])
    original=quote_imports.insert_quote
    def fail_second(session,dataset_id,q,supplier):
        if q.sku=="CBL-002":
            raise StateConflict("controlled second-row failure")
        return original(session,dataset_id,q,supplier)
    monkeypatch.setattr(quote_imports,"insert_quote",fail_second)
    with pytest.raises(StateConflict):
        quote_imports.confirm_upload(db,identifier,body,"test")
    assert not quote_history(db,dataset)
    monkeypatch.setattr(quote_imports,"insert_quote",original)
    other=Database(settings.runtime_dir/"restockiq.sqlite3")
    with ThreadPoolExecutor(2) as pool:
        a=pool.submit(quote_imports.confirm_upload,db,identifier,body,"test")
        b=pool.submit(quote_imports.confirm_upload,other,identifier,body,"test")
        assert a.result()==b.result()
    assert len(quote_history(db,dataset))==2
    other.engine.dispose()


def test_quantity_tiers_and_combined_input_attribution(db,settings,dataset):
    refresh_fx(db,settings,RecordedProvider(),NOW)
    create_quote(db,dataset,modified_quote(min_quantity=1,max_quantity=9),"test")
    create_quote(db,dataset,modified_quote(min_quantity=10,max_quantity=20,unit_price="9"),"test")
    save_settings(db,dataset,RateSettings(order_quantity=12),"test")
    first=create_assessment(db,settings,dataset,"CHG-001",NOW)
    assert first["products"][0]["replacement_cost_pkr"]=="2720.00"
    save_settings(db,dataset,RateSettings(order_quantity=12,spread_percent="1"),"test")
    second=create_assessment(db,settings,dataset,"CHG-001",NOW)
    comparison=compare_assessments(db,first["id"],second["id"])["products"][0]
    assert comparison["attribution"]=="combined inputs"
    assert not comparison["alerts"]


def test_rate_limit_cooldown_and_unavailable_cache(db,settings):
    class Limited:
        def __init__(self): self.calls=0
        def fetch(self,now):
            self.calls+=1
            raise ProviderError("429; recorded rate limit",now+timedelta(hours=3))
    limited=Limited()
    failed=refresh_fx(db,settings,limited,NOW)
    assert failed["status"]=="unavailable" and failed["refresh_failed"]
    assert failed["next_attempt_at"]==instant(NOW+timedelta(hours=3))
    refresh_fx(db,settings,limited,NOW+timedelta(hours=1))
    assert limited.calls==1


def test_upload_expiration_cleanup_and_xlsx_formulas(db,settings,dataset):
    from app.db.market import QuoteUpload,QuoteChunk
    identifier,preview=upload(db,dataset,quote_csv([quote().model_dump(mode="json")]))
    quote_imports.cleanup_uploads(db,NOW+timedelta(days=2))
    with db.sessions() as session:
        assert session.get(QuoteUpload,identifier) is None
        assert not list(session.scalars(select(QuoteChunk)))
    payload=xlsx_payload([quote_imports.FIELDS])
    source=io.BytesIO()
    with ZipFile(io.BytesIO(payload)) as original,ZipFile(source,"w") as modified:
        for item in original.infolist():
            content=original.read(item.filename)
            if item.filename.endswith("sheet1.xml"):
                content=content.replace(b"</sheetData>",b'<row r="2"><c r="A2"><f>1+1</f><v>2</v></c></row></sheetData>')
            modified.writestr(item.filename,content)
    _,bad=upload(db,dataset,source.getvalue(),"quotes.xlsx")
    assert not bad["can_confirm"] and "Formulas" in bad["findings"][0]["message"]


def test_single_sku_fee_never_repeated_across_products(db,settings,dataset):
    create_quote(db,dataset,quote(),"test")
    create_quote(db,dataset,modified_quote(sku="CBL-002",supplier_id="SUP-02"),"test")
    refresh_fx(db,settings,RecordedProvider(),NOW)
    # Both products accept 12 inventory units; only the selected SKU pays this batch's fee.
    save_settings(db,dataset,RateSettings(order_quantity=12,payment_fee_total_pkr="120",payment_fee_sku="CHG-001"),"test")
    result=create_assessment(db,settings,dataset,now=NOW)
    products={p["sku"]:p for p in result["products"]}
    assert products["CHG-001"]["components"]["allocated_payment_fee_unit_pkr"]=="10"
    assert products["CBL-002"]["components"]["allocated_payment_fee_unit_pkr"]=="0"
    assert sum(D(p["components"].get("payment_fee_total_pkr","0")) for p in result["products"])==120
    with pytest.raises(ValidationError):
        RateSettings(order_quantity=12,payment_fee_total_pkr="120")
    with pytest.raises(StateConflict):
        save_settings(db,dataset,RateSettings(order_quantity=12,payment_fee_total_pkr="120",payment_fee_sku="unknown"),"test")


def test_unchanged_financial_inputs_do_not_make_daily_duplicates(db,settings,dataset):
    create_quote(db,dataset,quote(),"test")
    refresh_fx(db,settings,RecordedProvider(),NOW)
    a=create_assessment(db,settings,dataset,"CHG-001",NOW)
    b=create_assessment(db,settings,dataset,"CHG-001",NOW+timedelta(days=1))
    assert a["id"]==b["id"]
    # Reference market movements must not create snapshots for manual-bank pricing.
    save_settings(db,dataset,RateSettings(mode="manual_bank",manual_rate="310",manual_source="bank",
                  manual_observed_at=OBSERVED,manual_valid_until=NOW+timedelta(days=5)),"test")
    a=create_assessment(db,settings,dataset,"CHG-001",NOW)
    refresh_fx(db,settings,RecordedProvider("320",NOW+timedelta(days=1)),NOW+timedelta(days=1,seconds=1))
    b=create_assessment(db,settings,dataset,"CHG-001",NOW+timedelta(days=1,seconds=1))
    assert a["id"]==b["id"]
