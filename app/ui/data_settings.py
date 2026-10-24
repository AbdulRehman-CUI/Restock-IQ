from datetime import date
import streamlit as st
from app.ui.common import saved_get, error, header, table, day
from app.ui.api_client import APIError


@st.fragment(run_every="2s")
def poll_job(client, job_id):
    try:
        job = client.get(f"jobs/{job_id}")
    except APIError as exc:
        error(exc)
        return
    status = job['status']
    stage = job['stage'].replace('_', ' ').capitalize()
    st.caption(f"Current operation · {status.capitalize()} · {stage}")
    if job.get('progress') is not None:
        st.progress(job["progress"] / 100, text=f"{job['progress']}% · {stage}")
    else:
        st.info(f"{stage} · Waiting for an update from the API.")
    if status in ("completed", "failed"):
        st.session_state["last_job"] = job
        st.session_state.pop("job", None)
        if status == "completed":
            st.session_state["pending_dataset"] = job["dataset_id"]
            if job.get("model_id"):
                st.session_state["pending_model"] = job["model_id"]
        st.rerun()


def render(client, context):
    header("Data & Settings", "Set up data, check its quality, and create a saved inventory analysis.")
    if st.session_state.get("job"):
        poll_job(client, st.session_state["job"])
    if st.session_state.get("last_job"):
        last = st.session_state["last_job"]
        if last['status'] == 'failed':
            st.error(last.get('error') or 'The last operation failed.')
            st.caption("Correct the issue, then explicitly submit the relevant step again. Refresh discovery after a timeout before retrying. Job ID: " + last['id'])
        else:
            st.caption(f"Last operation · {last['status'].capitalize()} · {last['stage'].replace('_', ' ')}")
    busy = bool(st.session_state.get("job"))
    dataset_id = context.get('dataset')
    stages = [("1 · Choose or create data", bool(dataset_id), True),
              ("2 · Review data quality", bool(dataset_id), bool(dataset_id)),
              ("3 · Train a model", bool(context.get('model')), bool(dataset_id)),
              ("4 · Generate a forecast", bool(context.get('forecast')), bool(context.get('model'))),
              ("5 · Create an analysis", bool(context.get('recommendation')), bool(context.get('forecast')))]
    st.caption("Setup workflow")
    table([{"Step": label, "Status": ('Available to review' if label.startswith('2') else 'Saved result selected') if done else 'Ready' if ready else 'Needs previous step'}
           for label, done, ready in stages])
    with st.container(border=True):
        st.subheader("1 · Choose or create data")
        st.caption("Choose existing data in the sidebar, or create a separate dataset below. Every submission is explicit.")
        demo, upload = st.tabs(["Synthetic dataset", "Upload CSV files"])
        with demo:
            st.caption("Fictional business records and FX rates. Synthetic results do not establish real savings.")
            with st.form("demo"):
                count = st.number_input("Product count", 1, 200, 20)
                with st.expander("Advanced generation settings"):
                    seed = st.number_input("Seed", 0, 2**32 - 1, 42)
                    start = st.date_input("History start", date(2025, 10, 16))
                    days = st.number_input("History days", 1, 3650, 365)
                submit = st.form_submit_button("Generate demo dataset", disabled=busy)
            if submit:
                job = client.post("datasets/demo", {"seed": seed, "start_date": start.isoformat(), "days": days, "sku_count": count})
                st.session_state["job"] = job["job_id"]
                st.rerun()
        with upload:
            contracts = saved_get(client.base_url, "data-contracts")
            st.caption("All six files are required and validated together. Invalid batches create no dataset. Default maximum batch: 12 MiB, configurable on the API.")
            with st.form("upload"):
                columns = st.columns(2)
                uploads = {name: columns[i % 2].file_uploader(name, type="csv", key="upload_" + name) for i, name in enumerate(contracts)}
                missing = [name for name, uploaded in uploads.items() if uploaded is None]
                st.caption("Required files not yet selected: " + ", ".join(missing) if missing else "All six files selected.")
                st.caption("The file checklist updates when this form is submitted.")
                submit = st.form_submit_button("Validate and import dataset", disabled=busy)
            if submit:
                if missing:
                    st.error("Choose the missing files: " + ", ".join(missing))
                else:
                    with st.spinner("Validating six files & importing dataset…"):
                        result = client.request("POST", "datasets/import", files={name.removesuffix(".csv"): (name, f.getvalue(), "text/csv") for name, f in uploads.items()}).json()
                    st.session_state["pending_dataset"] = str(result["dataset_id"])
                    st.rerun()
            with st.expander("CSV fields & defaults"):
                for filename, fields in contracts.items():
                    st.write(filename)
                    st.caption("Required: " + ', '.join(fields['required_fields']))
                    if fields['optional_fields']:
                        st.caption("Optional · backend defaults: " + ', '.join(fields['optional_fields']))
    if not dataset_id:
        st.info("No dataset selected. Generate or import data to enable the remaining steps.")
        return
    quality = saved_get(client.base_url, f"datasets/{dataset_id}/summary")
    with st.container(border=True):
        st.subheader("2 · Review data quality")
        st.caption(f"{quality['product_count']} products · {day(quality['date_range']['start'])} – {day(quality['date_range']['end'])} · {quality['manifest']['provenance'].title()} data")
        table([{"Check": 'Sales coverage (SKU-days)', "Result": f"{quality['sales_coverage']['actual_sku_days']:,} / {quality['sales_coverage']['expected_sku_days']:,}"},
               {"Check": 'Stock-constrained open SKU-days', "Result": str(quality['stockout_affected_sku_days'])},
               {"Check": 'Closed SKU-days', "Result": str(quality['closed_store_sku_days'])},
               {"Check": 'Available inventory units', "Result": str(quality['inventory']['available_units'])},
               {"Check": 'Cost inputs', "Result": 'Available' if quality['readiness']['current_cost_inputs_available'] else 'Some inputs unavailable'}])
        flags = quality['readiness']['cost_blockers'] + quality['warnings']
        if flags:
            table([{"Issue": flag['code'].replace('_', ' ').capitalize(),
                    "Detail": flag.get('message') or ', '.join(flag.get('items', []))} for flag in flags])
        else:
            st.success("No data-quality warnings reported.")
        st.caption("Schema validity does not establish model sufficiency. Freshness is measured at the dataset's analysis date.")
        with st.expander("Full quality report & provenance"):
            st.json(quality)
    with st.container(border=True):
        st.subheader("3 · Train a model")
        st.caption("Prerequisite: selected dataset. Existing completed models are available in Advanced analysis selection.")
        with st.form("train"):
            with st.expander("Advanced training settings"):
                seed = st.number_input("Training seed", 0, 2**32 - 1, 42)
            submitted = st.form_submit_button("Train and evaluate", disabled=busy)
        if submitted:
            job = client.post("models/train", {"dataset_id": dataset_id, "seed": seed})
            st.session_state["job"] = job["job_id"]
            st.rerun()
    with st.container(border=True):
        st.subheader("4 · Generate a forecast")
        st.caption("Prerequisite: completed model. Recommendations require the origin to match the inventory snapshot.")
        with st.form("create_forecast_form"):
            horizon = st.number_input("Forecast days", 1, 30, 30)
            with st.expander("Advanced forecast settings"):
                origin = st.date_input("Analysis origin (inventory snapshot date)", date.fromisoformat(context['dataset_row']['end_date']))
            submitted = st.form_submit_button("Generate forecast", disabled=busy or not bool(context.get("model")))
        if submitted:
            with st.spinner("Generating forecast…"):
                forecast = client.post("forecasts", {"dataset_id": dataset_id, "model_id": context["model"], "origin": origin.isoformat(), "horizon_days": horizon})
            st.session_state["pending_forecast"] = forecast["forecast_run_id"]
            st.rerun()
    with st.container(border=True):
        st.subheader("5 · Create an analysis")
        st.caption("Prerequisite: compatible forecast. Saving creates a new immutable base analysis; existing results retain their settings.")
        with st.form("recommend"):
            with st.expander("Advanced pricing settings"):
                step = st.number_input("Price rounding step (PKR)", min_value=.01, value=5.0, step=1.0, key="draft_step")
                cap_enabled = st.checkbox("Limit suggested price changes", key="draft_cap_enabled")
                cap = st.number_input("Maximum price change (%)", min_value=0.0, max_value=10000.0, value=25.0, key="draft_cap")
            submitted = st.form_submit_button("Generate base recommendations", type="primary", disabled=busy or not bool(context.get("forecast")))
        if submitted:
            with st.spinner("Calculating inventory & pricing decisions…"):
                run = client.post("recommendations", {"dataset_id": dataset_id, "forecast_run_id": context["forecast"],
                    "pricing": {"price_step_pkr": str(step), "max_price_change_pct": str(cap) if cap_enabled else None}})
            st.session_state["pending_run"] = run["recommendation_run_id"]
            st.session_state["draft_created_run"] = run["recommendation_run_id"]
            st.rerun()
    if st.session_state.get("draft_created_run"):
        st.success("Base analysis saved. Open Overview to inspect the selected results.")
        st.button("Open Overview", on_click=lambda: st.session_state.update(page="Overview"))
