"""Local presentation layer. All business data is obtained through FastAPI."""
import os
from html import escape
import streamlit as st
from app.ui.api_client import APIClient, APIError
from app.ui.common import choose, saved_get, saved_download, error, day, theme
from app.ui.state import reset_dataset, selected_exports
from app.ui import overview, product, scenario, evaluation, data_settings

st.set_page_config(page_title="RestockIQ · Inventory intelligence", page_icon="▣", layout="wide", initial_sidebar_state="expanded")
theme()

PAGES = {"Overview": overview, "Products": product, "Scenarios": scenario,
         "Model Performance": evaluation, "Data & Settings": data_settings}


def main():
    with st.sidebar:
        st.markdown('<div class="rq-brand">Restock<span>IQ</span></div>', unsafe_allow_html=True)
        st.caption("Inventory & pricing intelligence")
        if st.session_state.get("navigate"):
            st.session_state["page"] = st.session_state.pop("navigate")
        page = st.radio("Workspace", list(PAGES), key="page", label_visibility="collapsed")
        with st.expander("Connection settings"):
            api_url = st.text_input("Local API address", value=os.getenv("RESTOCKIQ_API_URL", "http://127.0.0.1:8000"), key="api_url")
            if st.button("Refresh connection"):
                st.rerun()
        client = APIClient(api_url)
        if st.session_state.get("previous_api") != api_url:
            reset_dataset(st.session_state)
            st.session_state.pop("dataset", None)
            st.session_state["previous_api"] = api_url
        datasets = client.get("datasets")
        st.caption("● Connected to local API")
        for key in ("dataset", "model", "forecast", "run"):
            pending = st.session_state.pop("pending_" + key, None)
            if pending:
                if key == "dataset" and st.session_state.get("dataset") != pending:
                    reset_dataset(st.session_state)
                st.session_state[key] = pending
        st.subheader("Active analysis")
        dataset_id = choose("Dataset", datasets, "dataset", lambda r: f"{r['provenance'].title()} · {day(r['end_date'])} · {r['id'][:8]}")
        if st.session_state.get("previous_dataset") != dataset_id:
            reset_dataset(st.session_state)
        st.session_state["previous_dataset"] = dataset_id
        context = {"dataset": dataset_id, "runs": []}
        if dataset_id:
            context["dataset_row"] = next(r for r in datasets if r["id"] == dataset_id)
            with st.expander("Advanced analysis selection"):
                st.caption("Select exact saved results. Short references identify runs; full IDs are in technical details.")
                models = client.get("models", dataset_id=dataset_id)
                completed = [m for m in models if m["status"] == "completed"]
                model_id = choose("Completed model", completed, "model")
                if st.session_state.get("previous_model") != model_id:
                    for key in ("forecast", "run", "product", "base_run", "saved_scenario", "scenario_result", "scenario_created", "scenario_selected_before"):
                        st.session_state.pop(key, None)
                st.session_state["previous_model"] = model_id
                context["model"] = model_id
                forecasts = client.get("forecasts", dataset_id=dataset_id, model_id=model_id) if model_id else []
                forecast_id = choose("Forecast", forecasts, "forecast")
                if st.session_state.get("previous_forecast") != forecast_id:
                    for key in ("run", "product", "base_run", "saved_scenario", "scenario_result", "scenario_created", "scenario_selected_before"):
                        st.session_state.pop(key, None)
                st.session_state["previous_forecast"] = forecast_id
                context["forecast"] = forecast_id
                if forecast_id:
                    context["forecast_data"] = saved_get(client.base_url, f"forecasts/{forecast_id}")
                    runs = client.get("recommendations", dataset_id=dataset_id, forecast_run_id=forecast_id)
                    context["runs"] = runs
                    run_id = choose("Saved analysis", runs, "run")
                    if run_id:
                        context["recommendation"] = saved_get(client.base_url, f"recommendations/{run_id}")
        forecast = context.get("forecast_data")
        run = context.get("recommendation")
        if forecast:
            st.caption(f"Analysis date · {day(forecast['origin'])}\n\nHorizon · {forecast['horizon_days']} days")
        else:
            st.caption("Choose or create a forecast in Data & Settings.")
        if not dataset_id:
            st.caption("Start in Data & Settings to create the first dataset.")
    if dataset_id:
        row = context["dataset_row"]
        active = run.get("scenario") if run else None
        provenance = "Synthetic data · fictional records and FX" if row['provenance'] == 'synthetic' else "Uploaded data"
        title = active['name'] if active else "Base analysis" if run else "Analysis setup"
        kind = 'Hypothetical scenario · ' if active else ''
        date_label = f" · {day(forecast['origin'])} · {forecast['horizon_days']}-day horizon" if forecast else " · Forecast not selected"
        st.markdown(f'<div class="rq-context"><span class="rq-badge">{escape(provenance)}</span>'
                    f'{escape(kind + title + date_label)}</div>', unsafe_allow_html=True)
    PAGES[page].render(client, context)
    if dataset_id:
        with st.expander("Technical details · selected analysis"):
            st.json({"dataset_id": dataset_id, "model_id": context.get("model"), "forecast_run_id": context.get("forecast"),
                "recommendation_run_id": run["recommendation_run_id"] if run else None,
                "scenario": run.get("scenario") if run else None, "effective_settings": run.get("effective_settings") if run else None,
                "assumptions": run.get("assumptions") if run else None})
    if context.get("forecast"):
        with st.expander("Download selected reports"):
            st.caption("Reports match the selected analysis above and preserve exact precision and unavailable values.")
            for label, path, filename in selected_exports(context.get("forecast"), context.get("recommendation", {}).get("recommendation_run_id")):
                st.download_button(label, saved_download(client.base_url, path), file_name=filename,
                    mime="text/csv" if filename.endswith("csv") else "application/json", key=path)


try:
    main()
except APIError as exc:
    error(exc)
    st.info("Check Connection settings in the sidebar. Reconnect and refresh to resume your saved analysis.")
