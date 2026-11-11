"""Demo pricing routes and separately authorized scheduled refresh."""
import csv
import hmac
import io
import json
import os
from uuid import UUID
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select

from app.api.schemas.market import (RateSettings, QuoteInput, ReconfirmQuote, AssessmentRequest, UploadStart,
    ConfirmImport, FXStatus, FXRateView, SettingsView, QuoteView, AssessmentView, ComparisonView,
    UploadTicket, ChunkReceipt, ImportPreview, ImportReceipt, AssessmentSummary, ProductAlert, ScheduledResult)
from app.db.market import FXObservation, PricingAssessment
from app.services import market, quote_imports
from app.services.fx import current_fx, refresh_fx, observation_view, with_demo_fx
from app.services.exports import spreadsheet_safe

router = APIRouter(prefix="/api/v1/market", tags=["current market and pricing"])
import_router = APIRouter(prefix=router.prefix, tags=router.tags)


def identity(request):
    return "public-demo" if request.app.state.settings.cloud_mode else "local-operator"


@router.get("/fx", response_model=FXStatus)
def status(request: Request, dataset_id: UUID | None = None):
    return current_fx(request.app.state.db, request.app.state.settings, dataset_id)


@router.post("/fx/refresh", response_model=FXStatus)
def refresh(request: Request, dataset_id: UUID | None = None):
    db = request.app.state.db
    return with_demo_fx(db, refresh_fx(db, request.app.state.settings), dataset_id)


@router.get("/fx/history", response_model=list[FXRateView])
def fx_history(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=10000)):
    with request.app.state.db.sessions() as session:
        return [observation_view(row) for row in session.scalars(select(FXObservation)
                .order_by(FXObservation.observed_at.desc()).limit(limit).offset(offset))]


@router.get("/datasets/{dataset_id}/settings", response_model=SettingsView)
def settings(dataset_id: UUID, request: Request):
    return market.get_settings(request.app.state.db, dataset_id)


@router.post("/datasets/{dataset_id}/settings", response_model=SettingsView, status_code=201)
def settings_save(dataset_id: UUID, body: RateSettings, request: Request):
    return market.save_settings(request.app.state.db, dataset_id, body, identity(request),
                                can_configure_schedule=not request.app.state.settings.cloud_mode)


@router.get("/datasets/{dataset_id}/catalog")
def catalog(dataset_id: UUID, request: Request):
    from app.db.models import Product
    with request.app.state.db.sessions() as session:
        dataset = market.dataset_record(session, dataset_id)
        products = list(session.scalars(select(Product).where(Product.dataset_id == str(dataset_id)).order_by(Product.sku)))
        return {"provenance": dataset.provenance, "dataset_origin": dataset.end_date,
                "products": [{"sku": p.sku, "name": p.name, "supplier_id": p.supplier_id, "pack_size": p.pack_size,
                              "moq": p.moq} for p in products]}


@router.get("/datasets/{dataset_id}/quotes", response_model=list[QuoteView])
def quotes(dataset_id: UUID, request: Request, sku: str | None = Query(None, max_length=200),
           limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=10000)):
    return market.quote_history(request.app.state.db, dataset_id, sku, limit, offset)


@router.post("/datasets/{dataset_id}/quotes", response_model=QuoteView, status_code=201)
def quote_create(dataset_id: UUID, body: QuoteInput, request: Request, idempotency_key: UUID | None = Header(None)):
    return market.create_quote(request.app.state.db, dataset_id, body, identity(request), str(idempotency_key) if idempotency_key else None)


@router.post("/datasets/{dataset_id}/quotes/{quote_id}/reconfirm", response_model=QuoteView, status_code=201)
def quote_reconfirm(dataset_id: UUID, quote_id: UUID, body: ReconfirmQuote, request: Request,
                    idempotency_key: UUID | None = Header(None)):
    return market.reconfirm_quote(request.app.state.db, dataset_id, quote_id, body, identity(request),
                                  str(idempotency_key) if idempotency_key else None)


@router.get("/quote-template.csv")
def quote_template():
    return Response(quote_imports.template_csv(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="supplier-price-list-template.csv"'})


@import_router.post("/datasets/{dataset_id}/quote-imports", status_code=201, response_model=UploadTicket)
def start(dataset_id: UUID, body: UploadStart, request: Request):
    return quote_imports.start_upload(request.app.state.db, dataset_id, body, request.app.state.settings.max_upload_bytes)


@import_router.put("/quote-imports/{identifier}/chunks/{position}", response_model=ChunkReceipt)
async def chunk(identifier: UUID, position: int, request: Request):
    data = bytearray()
    async for part in request.stream():
        data.extend(part)
        if len(data) > quote_imports.CHUNK_BYTES:
            raise HTTPException(413, "Quote chunks must not exceed 512 KiB")
    return quote_imports.put_chunk(request.app.state.db, identifier, position, bytes(data))


@import_router.post("/quote-imports/{identifier}/preview", response_model=ImportPreview)
def preview(identifier: UUID, request: Request):
    return quote_imports.preview_upload(request.app.state.db, identifier)


@import_router.post("/quote-imports/{identifier}/confirm", status_code=201, response_model=ImportReceipt)
def confirm(identifier: UUID, body: ConfirmImport, request: Request):
    return quote_imports.confirm_upload(request.app.state.db, identifier, body, identity(request))


@router.post("/datasets/{dataset_id}/assessments", response_model=AssessmentView, status_code=201)
def assess(dataset_id: UUID, body: AssessmentRequest, request: Request):
    return market.create_assessment(request.app.state.db, request.app.state.settings, dataset_id, body.sku)


@router.get("/datasets/{dataset_id}/assessments", response_model=list[AssessmentSummary])
def history(dataset_id: UUID, request: Request, limit: int = Query(50, ge=1, le=100),
            offset: int = Query(0, ge=0, le=10000), sku: str | None = Query(None, max_length=200)):
    with request.app.state.db.sessions() as session:
        market.dataset_record(session, dataset_id)
        # Page the SQL query before inspecting SKU membership; never load unbounded result blobs.
        rows = session.scalars(select(PricingAssessment).where(PricingAssessment.dataset_id == str(dataset_id))
                               .order_by(PricingAssessment.created_at.desc()).limit(limit).offset(offset))
        return [{"id": r.id, "assessed_at": r.created_at, "label": r.result["label"],
                 "products": [p["sku"] for p in r.result["products"]]} for r in rows
                if not sku or any(p["sku"] == sku for p in r.result["products"])]


@router.get("/assessments/{identifier}", response_model=AssessmentView)
def detail(identifier: UUID, request: Request):
    return market.get_assessment(request.app.state.db, identifier)


@router.get("/assessments/{before}/compare/{after}", response_model=ComparisonView)
def compare(before: UUID, after: UUID, request: Request):
    return market.compare_assessments(request.app.state.db, before, after)


@router.get("/assessments/{identifier}/alerts", response_model=list[ProductAlert])
def alerts(identifier: UUID, request: Request):
    assessment = market.get_assessment(request.app.state.db, identifier)
    return [{"sku": p["sku"], "alerts": p["warnings"], "assumptions": p["assumptions"]}
            for p in assessment["products"] if p["warnings"]]


@router.get("/assessments/{identifier}/export")
def export(identifier: UUID, request: Request, format: str = Query("json", pattern="^(json|csv)$")):
    value = market.get_assessment(request.app.state.db, identifier)
    if format == "json":
        return Response(json.dumps(value, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="current-pricing-{identifier}.json"'})
    output = io.StringIO(newline="")
    columns = ["sku", "name", "label", "assessed_at", "dataset_id", "assessment_id", "dataset_origin", "calculation_version",
               "replacement_cost_pkr", "unrounded_replacement_cost_pkr", "minimum_replacement_funding_price_pkr", "unrounded_funding_shortfall_unit_pkr",
               "current_price_pkr", "historical_unit_cost_pkr", "historical_gross_difference_pkr", "suggested_price_pkr",
               "unrounded_target_price_pkr", "funding_shortfall_unit_pkr", "current_replacement_margin", "achievable_margin",
               "price_change_pkr", "price_change_pct", "quote_json", "fx_json", "rate_selection_json", "settings_json", "components_json", "warnings", "assumptions"]
    writer = csv.DictWriter(output, columns, lineterminator="\n")
    writer.writeheader()
    for p in value["products"]:
        row = {key: p.get(key) for key in columns if key in p}
        row.update({key: p["pricing"].get(key) for key in columns if key in p["pricing"]})
        row.update(label=value["label"], assessed_at=value["assessed_at"], dataset_id=value["dataset_id"], assessment_id=value["id"],
                   dataset_origin=value["context"]["dataset_origin"], calculation_version=value["calculation_version"],
                   quote_json=json.dumps(p["quote"]), fx_json=json.dumps(value["fx"]), rate_selection_json=json.dumps(value["rate_selection"]), settings_json=json.dumps(value["settings"]),
                   components_json=json.dumps(p["components"]), warnings="|".join(p["warnings"]),
                   assumptions=" | ".join(value["assumptions"] + p["assumptions"]))
        # Numeric Decimal strings retain their exact sign and precision; guard all free-text fields.
        for key in ("sku", "name", "label", "warnings", "assumptions"):
            row[key] = spreadsheet_safe(row[key])
        writer.writerow(row)
    return Response(output.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="current-pricing-{identifier}.csv"'})


@router.get("/scheduled-refresh", response_model=ScheduledResult)
def scheduled_refresh(request: Request, authorization: str = Header("")):
    secret = os.environ.get("CRON_SECRET", "")
    if not secret:
        raise HTTPException(503, "Scheduled FX refresh is not configured")
    if not hmac.compare_digest(authorization.encode(), ("Bearer " + secret).encode()):
        raise HTTPException(401, "Invalid scheduled-refresh authorization")
    db, settings = request.app.state.db, request.app.state.settings
    status = refresh_fx(db, settings)
    quote_imports.cleanup_uploads(db)
    assessments = market.auto_assess(db, settings)
    # Never redistribute raw provider observations from the scheduling endpoint.
    return {"fx_status": status["status"], "refresh_failed": status["refresh_failed"], "assessment_ids": assessments}
