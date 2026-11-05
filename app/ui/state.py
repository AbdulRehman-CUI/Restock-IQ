"""Small, testable selection and request conversions."""
from decimal import Decimal

DEPENDENTS = ("model", "forecast", "run", "product", "base_run", "saved_scenario", "scenario_result", "previous_forecast", "previous_model", "last_job",
              "resolved_run", "manual_dependencies", "report_selection", "report_ready", "setup_origin", "setup_horizon")


def reset_dataset(state):
    for key in DEPENDENTS:
        state.pop(key, None)
    for key in list(state):
        if key.startswith(("draft_", "action_", "scenario_", "pending_")):
            state.pop(key, None)


def reset_analysis_views(state):
    for key in list(state):
        if key.startswith(('scenario_', 'action_')) or key in ('product', 'base_run', 'saved_scenario', 'report_ready'):
            state.pop(key, None)


def resolve_analysis(state, row):
    """A saved analysis owns its dependencies; never combine independent selections."""
    if state.get('dataset') != row['dataset_id']:
        reset_dataset(state)
        state['dataset'] = row['dataset_id']
    if state.get('resolved_run') != row['id']:
        reset_analysis_views(state)
    state.update(model=row['model_id'], forecast=row['forecast_run_id'], resolved_run=row['id'])


def demo_analysis(rows):
    return next((row for row in rows if row.get('provenance') == 'synthetic' and
                 not row.get('scenario_id') and row.get('horizon_days') == 30), None)


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
