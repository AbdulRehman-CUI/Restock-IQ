"""Small, testable selection and request conversions."""
from decimal import Decimal

DEPENDENTS = ("model", "forecast", "run", "product", "base_run", "saved_scenario", "scenario_result", "job", "previous_forecast", "previous_model", "last_job")


def reset_dataset(state):
    for key in DEPENDENTS:
        state.pop(key, None)
    for key in list(state):
        if key.startswith(("draft_", "action_", "scenario_")):
            state.pop(key, None)


def margin_fraction(percent, enabled):
    return str(Decimal(str(percent)) / 100) if enabled else None


def selected_exports(forecast_id, run_id):
    result = []
    if forecast_id:
        result += [("Forecast CSV", f"forecasts/{forecast_id}/export", f"forecast-{forecast_id}.csv"),
                   ("Forecast metadata", f"forecasts/{forecast_id}/metadata", f"forecast-{forecast_id}.json")]
    if run_id:
        result += [("Recommendation CSV", f"recommendations/{run_id}/export", f"recommendations-{run_id}.csv"),
                   ("Recommendation metadata", f"recommendations/{run_id}/metadata", f"recommendations-{run_id}.json")]
    return result
