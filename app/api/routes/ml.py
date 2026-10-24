from uuid import UUID
from fastapi import APIRouter, Request
from fastapi.responses import Response
from app.api.schemas import JobAccepted
from app.api.schemas.ml import ForecastRequest, ForecastResponse, ModelResponse, TrainRequest
from app.services.forecasting import create_forecast, csv_text, get_forecast, model_metrics

router = APIRouter(prefix="/api/v1", tags=["demand forecasting"])


@router.post("/models/train", response_model=JobAccepted, status_code=202)
def train(body: TrainRequest, request: Request):
    job = request.app.state.worker.submit_training(body.dataset_id, body.seed)
    return {"job_id": job["id"], "status_url": f"/api/v1/jobs/{job['id']}"}


@router.get("/models/{model_id}/metrics", response_model=ModelResponse)
def metrics(model_id: UUID, request: Request):
    return model_metrics(request.app.state.db, model_id)


@router.post("/forecasts", response_model=ForecastResponse, status_code=201)
def forecast(body: ForecastRequest, request: Request):
    return create_forecast(request.app.state.db, request.app.state.settings, str(body.dataset_id),
                           str(body.model_id), body.origin, body.horizon_days)


@router.get("/forecasts/{run_id}", response_model=ForecastResponse)
def persisted_forecast(run_id: UUID, request: Request):
    return get_forecast(request.app.state.db, run_id)


@router.get("/forecasts/{run_id}/export")
def forecast_export(run_id: UUID, request: Request):
    return Response(csv_text(get_forecast(request.app.state.db, run_id)), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="forecast-{run_id}.csv"'})
