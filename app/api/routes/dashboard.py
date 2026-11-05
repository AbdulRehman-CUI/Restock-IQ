"""Typed discovery, audit and operational-history endpoints for the local UI."""
from datetime import date
from typing import Literal, Any
from uuid import UUID
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from app.services import dashboard
from app.services.recommendations import get_recommendations
from app.services.forecasting import get_forecast
from app.data.contracts import CONTRACTS
from fastapi.responses import Response
import csv
import io
import zipfile

router = APIRouter(prefix="/api/v1", tags=["dashboard"])


class DiscoveryItem(BaseModel):
    id: UUID
    dataset_id: UUID | None = None
    model_id: UUID | None = None
    forecast_run_id: UUID | None = None
    scenario_id: UUID | None = None
    provenance: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    origin: date | None = None
    training_cutoff: date | None = None
    horizon_days: int | None = None
    created_at: str | None = None
    name: str | None = None
    status: str | None = None
    role: str | None = None
    selected_method: str | None = None


@router.get("/datasets", response_model=list[DiscoveryItem], response_model_exclude_none=True)
def datasets(request: Request):
    return dashboard.discover(request.app.state.db, "datasets")


@router.get("/analyses", response_model=list[DiscoveryItem], response_model_exclude_none=True)
def analyses(request: Request):
    return dashboard.analyses(request.app.state.db)


@router.get("/models", response_model=list[DiscoveryItem], response_model_exclude_none=True)
def models(request: Request, dataset_id: UUID):
    return dashboard.discover(request.app.state.db, "models", dataset_id=dataset_id)


@router.get("/forecasts", response_model=list[DiscoveryItem], response_model_exclude_none=True)
def forecasts(request: Request, dataset_id: UUID, model_id: UUID | None = None):
    return dashboard.discover(request.app.state.db, "forecasts", dataset_id=dataset_id, model_id=model_id)


@router.get("/recommendations", response_model=list[DiscoveryItem], response_model_exclude_none=True)
def recommendations(request: Request, dataset_id: UUID, forecast_run_id: UUID | None = None):
    return dashboard.discover(request.app.state.db, "recommendations", dataset_id=dataset_id, forecast_run_id=forecast_run_id)


@router.get("/scenarios", response_model=list[DiscoveryItem], response_model_exclude_none=True)
def scenarios(request: Request, dataset_id: UUID, forecast_run_id: UUID | None = None):
    return dashboard.discover(request.app.state.db, "scenarios", dataset_id=dataset_id, forecast_run_id=forecast_run_id)


class SalesDay(BaseModel):
    date: date
    units_sold: int
    fully_in_stock: bool
    store_open: bool
    promo_active: bool


class History(BaseModel):
    dataset_id: UUID
    sku: str
    through: date
    rows: list[SalesDay]


@router.get("/datasets/{dataset_id}/products/{sku:path}/sales", response_model=History)
def history(dataset_id: UUID, sku: str, through: date, request: Request):
    return dashboard.sales_history(request.app.state.db, dataset_id, sku, through)


class Cash(BaseModel):
    value: str | None
    included_products: int
    excluded_products: int
    partial: bool
    scope: str


class Overview(BaseModel):
    order_now_products: int
    expedite_products: int
    below_target_margin_products: int
    shortage_products: int
    overdue_supply_products: int
    order_now_cash: Cash


@router.get("/recommendations/{run_id}/overview", response_model=Overview)
def overview(run_id: UUID, request: Request):
    return dashboard.overview_for_run(request.app.state.db, run_id)


@router.get("/recommendations/{run_id}/workspace")
def workspace(run_id: UUID, request: Request):
    return dashboard.workspace(request.app.state.db, run_id)


@router.get("/recommendations/{run_id}/catalog")
def product_catalog(run_id: UUID, request: Request):
    return dashboard.product_catalog(request.app.state.db, run_id)


@router.get("/forecasts/{run_id}/products/{sku:path}")
def product_forecast(run_id: UUID, sku: str, request: Request):
    return get_forecast(request.app.state.db, run_id, sku=sku)


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    sku: str = Field(min_length=1, max_length=200)
    action: Literal["reviewed", "rejected"]
    note: str = Field(default="", max_length=1000)


class Action(ActionRequest):
    run_id: UUID
    timestamp: str


@router.post("/recommendations/{run_id}/actions", response_model=Action, status_code=201)
def record(run_id: UUID, body: ActionRequest, request: Request):
    return dashboard.record_action(request.app.state.db, run_id, **body.model_dump())


@router.get("/recommendations/{run_id}/actions", response_model=list[Action])
def actions(run_id: UUID, request: Request):
    return dashboard.action_history(request.app.state.db, run_id)


@router.get("/forecasts/{run_id}/metadata", response_model=dict[str, Any])
def forecast_metadata(run_id: UUID, request: Request):
    return get_forecast(request.app.state.db, run_id, include_rows=False)


@router.get("/recommendations/{run_id}/metadata", response_model=dict[str, Any])
def recommendation_metadata(run_id: UUID, request: Request):
    return get_recommendations(request.app.state.db, run_id, include_products=False)


class CSVContract(BaseModel):
    required_fields: list[str]
    optional_fields: list[str]


@router.get("/data-contracts", response_model=dict[str, CSVContract])
def contracts():
    return {name: {"required_fields": [name for name, field in contract.model_fields.items() if field.is_required()],
                   "optional_fields": [name for name, field in contract.model_fields.items() if not field.is_required()]}
            for name, contract in CONTRACTS.items()}


@router.get('/data-contracts/templates')
def templates():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('README.txt', 'Blank examples of CSV structure, not usable business history.\n'
            'Fill all six files with your own records. Sales need a complete calendar per SKU; inventory uses the final sales date.\n'
            'Use ISO dates, timezone-aware quote timestamps, true/false flags, money with at most two decimals and margin fractions (0.20 = 20%).\n'
            'Empty quotes/orders/rates files still require headers. See the in-app field contract for required and optional columns.\n')
        for name, contract in CONTRACTS.items():
            header = io.StringIO(newline='')
            csv.writer(header, lineterminator='\n').writerow(contract.model_fields)
            archive.writestr(name, header.getvalue())
    return Response(output.getvalue(), media_type='application/zip',
        headers={'Content-Disposition': 'attachment; filename="marginshift-csv-templates.zip"'})
