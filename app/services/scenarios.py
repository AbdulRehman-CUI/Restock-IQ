from decimal import Decimal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field
from app.data.contracts import Fraction, Money
from app.core.errors import NotFound
from app.db.models import Scenario
from app.services.ingestion import utc_now


class ScenarioAssumptions(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)
    fx_change_pct: Decimal = Field(default=Decimal(0), gt=-100, le=10000, decimal_places=4, allow_inf_nan=False)
    supplier_price_change_pct: Decimal = Field(default=Decimal(0), gt=-100, le=10000, decimal_places=4, allow_inf_nan=False)
    additional_freight_pkr: Money = Decimal("0.00")
    extra_lead_days: int = Field(default=0, ge=0, le=3650, strict=True)
    target_margin_override: Fraction | None = None


SCENARIO_NOTICE = "Hypothetical assumptions, not predictions. Foreign supplier-price and FX changes compound; demand is unchanged."


def create_scenario(db, dataset_id, forecast_run_id, name, assumptions: ScenarioAssumptions):
    from app.services.recommendations import load_analysis_inputs
    load_analysis_inputs(db, dataset_id, forecast_run_id)
    name = name.strip()
    if not name or len(name) > 200:
        raise ValueError("Scenario name must contain 1–200 characters")
    assumptions = ScenarioAssumptions.model_validate(assumptions)
    scenario_id = str(uuid4())
    with db.transaction() as session:
        session.add(Scenario(id=scenario_id, dataset_id=str(dataset_id), forecast_run_id=str(forecast_run_id),
            name=name, assumptions=assumptions.model_dump(mode="json"), created_at=utc_now()))
    return get_scenario(db, scenario_id)


def get_scenario(db, scenario_id):
    with db.sessions() as session:
        scenario = session.get(Scenario, str(scenario_id))
        if scenario is None:
            raise NotFound("Scenario not found")
        return {"id": scenario.id, "dataset_id": scenario.dataset_id, "forecast_run_id": scenario.forecast_run_id,
                "name": scenario.name, "assumptions": scenario.assumptions,
                "created_at": scenario.created_at, "hypothetical": True, "notice": SCENARIO_NOTICE}
