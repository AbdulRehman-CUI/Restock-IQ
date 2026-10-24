from uuid import UUID
from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel
from app.api.schemas.decisions import (ComparisonResponse, ProductResult, RecommendationRequest,
                                       RecommendationResponse, ScenarioRequest, ScenarioResponse)
from app.services.scenarios import ScenarioAssumptions, create_scenario, get_scenario
from app.services.recommendations import create_recommendations, get_recommendations, get_product, compare_runs
from app.services.exports import recommendation_csv

router = APIRouter(prefix="/api/v1", tags=["decision engine"])


@router.post("/scenarios", status_code=201, response_model=ScenarioResponse)
def scenario(body: ScenarioRequest, request: Request):
    assumptions = ScenarioAssumptions.model_validate({key: getattr(body, key) for key in ScenarioAssumptions.model_fields})
    return create_scenario(request.app.state.db, body.dataset_id, body.forecast_run_id, body.name, assumptions)


@router.get("/scenarios/{scenario_id}", response_model=ScenarioResponse)
def scenario_detail(scenario_id: UUID, request: Request):
    return get_scenario(request.app.state.db, scenario_id)


@router.post("/recommendations", status_code=201, response_model=RecommendationResponse)
def recommend(body: RecommendationRequest, request: Request):
    return create_recommendations(request.app.state.db, request.app.state.settings,
        body.dataset_id, body.forecast_run_id, body.scenario_id, body.pricing)


@router.get("/recommendations/{run_id}", response_model=RecommendationResponse)
def recommendations(run_id: UUID, request: Request):
    return get_recommendations(request.app.state.db, run_id)


class ProductDetail(BaseModel):
    recommendation_run_id: UUID
    product: ProductResult


@router.get("/recommendations/{run_id}/products/{sku:path}", response_model=ProductDetail)
def product_detail(run_id: UUID, sku: str, request: Request):
    return get_product(request.app.state.db, run_id, sku)


@router.get("/recommendations/{run_id}/export")
def export(run_id: UUID, request: Request):
    return Response(recommendation_csv(get_recommendations(request.app.state.db, run_id)), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="recommendations-{run_id}.csv"'})


@router.get("/recommendations/{base_run_id}/compare/{scenario_run_id}", response_model=ComparisonResponse)
def comparison(base_run_id: UUID, scenario_run_id: UUID, request: Request):
    return compare_runs(request.app.state.db, base_run_id, scenario_run_id)
