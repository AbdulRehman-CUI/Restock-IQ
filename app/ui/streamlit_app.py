"""Local presentation layer. All business data is obtained through FastAPI."""
import os
from datetime import date, timedelta
from html import escape
import streamlit as st
from app.ui.api_client import APIClient, APIError
from app.ui.common import choose, saved_get, saved_download, error, day, theme, brand, header
from app.ui.state import reset_dataset, reset_analysis_views, resolve_analysis, demo_analysis, selected_exports
from app.ui import overview, product, scenario, evaluation, data_settings

st.set_page_config(page_title="MarginShift · Inventory intelligence", page_icon="▣", layout="wide", initial_sidebar_state="expanded")
theme()

PAGES = {"Overview": overview, "Products": product, "What-if Scenarios": scenario,
         "Model Performance": evaluation, "Data & Settings": data_settings}


def explore_demo(rows, datasets):
    row = demo_analysis(rows)
    if row:
        st.session_state.update(pending_dataset=row['dataset_id'], pending_run=row['id'], navigate='Overview')
    else:
        compatible = next((d for d in datasets if d['provenance'] == 'synthetic' and
            (date.fromisoformat(d['end_date']) - date.fromisoformat(d['start_date'])).days >= 364), None)
        if compatible:
            st.session_state['pending_dataset'] = compatible['id']
        st.session_state.update(navigate='Data & Settings', demo_requested='reuse' if compatible else 'new')


def reports(client, context):
    forecast_id = context.get('forecast')
    if not forecast_id:
        return
    run_id = context.get('recommendation', {}).get('recommendation_run_id')
    with st.expander('Download selected reports'):
        exports = selected_exports(forecast_id, run_id)
        selected = st.selectbox('Report', range(len(exports)), format_func=lambda i: exports[i][0])
        label, path, filename = exports[selected]
        st.caption('Reports preserve exact precision and unavailable values for the selected analysis.')
        if st.button('Prepare download'):
            with st.spinner('Preparing selected report…'):
                saved_download(client.base_url, path)
            st.session_state['report_ready'] = (client.base_url, path)
        if st.session_state.get('report_ready') == (client.base_url, path):
            st.download_button('Download ' + label, saved_download(client.base_url, path), file_name=filename,
                mime='text/csv' if filename.endswith('csv') else 'application/json', key=path)


def main():
    state = st.session_state
    # Streamlit removes widget-owned keys when an API failure hides those widgets.
    # Keep the selected context independently so reconnect restores exact saved IDs.
    for key, value in state.get('retained_context', {}).items():
        state[key] = state.get(key, value)
    # Keep drafts when their page/step is hidden; dataset transitions clear them.
    for key in list(state):
        if key.startswith(('draft_', 'scenario_')) or key in ('setup_horizon', 'product'):
            state[key] = state[key]
    with st.container(key='ms-navigation'):
        brand()
        if state.get('navigate'):
            state['page'] = state.pop('navigate')
        icons = dict(zip(PAGES, ['◈', '▦', '⇄', '▥', '⚙']))
        page = st.radio('Workspace', list(PAGES), key='page', horizontal=True,
                        format_func=lambda name: f"{icons[name]}  {name}", label_visibility='collapsed')
    with st.sidebar:
        st.caption('MARGINSHIFT / ANALYSIS LIBRARY')
        with st.expander('Connection settings'):
            api_url = st.text_input('Local API address', value=os.getenv('RESTOCKIQ_API_URL', 'http://127.0.0.1:8000'), key='api_url')
            st.button('Reconnect / refresh', help='Refresh saved analyses; successful writes are never retried.')
        client = APIClient(api_url)
        if state.get('previous_api') != client.base_url:
            reset_dataset(state)
            state.pop('dataset', None)
            state.pop('job', None)
            state.pop('retained_context', None)
            state['previous_api'] = client.base_url
        datasets = client.get('datasets')
        analyses = client.get('analyses')
        st.button('Explore demo', type='primary', use_container_width=True, on_click=explore_demo, args=(analyses, datasets))
        st.caption('Fictional records and FX · reuses saved results')
        pending = {key: state.pop('pending_' + key, None) for key in ('dataset', 'model', 'forecast', 'run')}
        if pending['dataset'] and state.get('dataset') != pending['dataset']:
            reset_dataset(state)
            state['dataset'] = pending['dataset']
        if state.get('dataset') not in [d['id'] for d in datasets] and datasets:
            preferred = demo_analysis(analyses)
            state['dataset'] = preferred['dataset_id'] if preferred else datasets[0]['id']
        analysis_slot = st.container()
        advanced = st.expander('Advanced analysis details')
        with advanced:
            st.caption('Choose data or prepare a new analysis. Exact saved references remain available below.')
            dataset_id = choose('Dataset', datasets, 'dataset', lambda r: f"{'Demo retailer' if r['provenance'] == 'synthetic' else 'Uploaded data'} · {day(r['end_date'])} · Data {datasets.index(r)+1}")
            if state.get('previous_dataset') != dataset_id:
                reset_dataset(state)
            state['previous_dataset'] = dataset_id
            for key in ('model', 'forecast', 'run'):
                if pending[key]:
                    state[key] = pending[key]
            if pending['model'] or pending['forecast']:
                state['manual_dependencies'] = True
            if pending['run']:
                state['manual_dependencies'] = False
            manual = st.checkbox('Choose model and forecast', key='manual_dependencies')
        state['retained_context'] = {key: state[key] for key in ('dataset', 'model', 'forecast', 'run', 'manual_dependencies') if key in state}
        rows = [r for r in analyses if r['dataset_id'] == dataset_id]
        if not manual and state.get('run') not in [r['id'] for r in rows] and rows:
            state['run'] = next((r['id'] for r in rows if not r.get('scenario_id')), rows[0]['id'])
        with analysis_slot:
            st.subheader('Active analysis')
            if manual:
                st.caption('Preparing a new analysis · switch off advanced selection to return to saved results.')
                run_id = None
            else:
                def label(row):
                    origin = 'Demo retailer' if row['provenance'] == 'synthetic' else 'Uploaded data'
                    kind = 'Scenario: ' + row['name'] if row.get('scenario_id') else 'Base'
                    return f"{origin} · {day(row['origin'])} · {row['horizon_days']}-day outlook · {kind} · Saved {rows.index(row)+1}"
                run_id = choose('Saved analysis', rows, 'run', label)
                if not rows:
                    st.caption('No saved analysis for this data. Use Data & Settings to prepare one.')
        context = {'dataset': dataset_id, 'runs': rows}
        if dataset_id:
            context['dataset_row'] = next(d for d in datasets if d['id'] == dataset_id)
            if run_id:
                row = next(r for r in rows if r['id'] == run_id)
                resolve_analysis(state, row)
                context.update(model=row['model_id'], forecast=row['forecast_run_id'])
                state['retained_context'] = {key: state[key] for key in ('dataset', 'model', 'forecast', 'run', 'manual_dependencies') if key in state}
                context['recommendation'] = saved_get(client.base_url, f'recommendations/{run_id}/metadata')
            else:
                with advanced:
                    models = client.get('models', dataset_id=dataset_id)
                    model_id = choose('Completed model', [m for m in models if m['status'] == 'completed'], 'model')
                    if state.get('previous_model') != model_id:
                        if not pending['forecast']:
                            state.pop('forecast', None)
                        state.pop('run', None)
                    state['previous_model'] = model_id
                    forecasts = client.get('forecasts', dataset_id=dataset_id, model_id=model_id) if model_id else []
                    forecast_id = choose('Forecast', forecasts, 'forecast')
                    if state.get('previous_forecast') != forecast_id:
                        reset_analysis_views(state)
                        state.pop('resolved_run', None)
                    state['previous_forecast'] = forecast_id
                    context.update(model=model_id, forecast=forecast_id)
            if context.get('forecast'):
                context['forecast_data'] = saved_get(client.base_url, f"forecasts/{context['forecast']}/metadata")
            context['runs'] = [r for r in rows if r['forecast_run_id'] == context.get('forecast')]
        forecast, run = context.get('forecast_data'), context.get('recommendation')
        state['retained_context'] = {key: state[key] for key in ('dataset', 'model', 'forecast', 'run', 'manual_dependencies') if key in state}
        if forecast:
            origin = date.fromisoformat(forecast['origin'])
            st.caption(f"Analysis date · {day(origin)}\n\nForecast · {day(origin + timedelta(days=1))} – {day(origin + timedelta(days=forecast['horizon_days']))}")
        if state.get('job') and page != 'Data & Settings':
            st.info('Preparation is running. Open Data & Settings for status and the next step.')
    if dataset_id:
        provenance = 'Synthetic data · fictional records and FX' if context['dataset_row']['provenance'] == 'synthetic' else 'Uploaded data'
        active = run.get('scenario') if run else None
        title = 'Hypothetical scenario · ' + active['name'] if active else 'Base analysis' if run else 'Analysis setup'
        date_label = f" · {day(forecast['origin'])} · {forecast['horizon_days']}-day outlook" if forecast else ''
        st.markdown(f'<div class="rq-context"><span class="rq-badge">{escape(provenance)}</span>{escape(title + date_label)}</div>', unsafe_allow_html=True)
    if page == 'Overview' and not run:
        header('Overview', 'Plan inventory and pricing using demand estimates, supplier costs, and delivery timing.')
        st.info('Create or select a saved analysis. Explore demo reuses compatible synthetic results; own data starts with six validated CSV files.')
        st.button('Use my own data', on_click=lambda: state.update(navigate='Data & Settings', setup_source='Upload CSV files', setup_step='Load data'))
        if not datasets:
            st.caption('No data is loaded yet. Explore demo opens guided preparation; training starts only when requested.')
    else:
        PAGES[page].render(client, context)
    if dataset_id:
        with st.expander('Technical details · selected analysis'):
            st.json({'dataset_id': dataset_id, 'model_id': context.get('model'), 'forecast_run_id': context.get('forecast'),
                'recommendation_run_id': run['recommendation_run_id'] if run else None,
                'scenario': run.get('scenario') if run else None, 'effective_settings': run.get('effective_settings') if run else None,
                'assumptions': run.get('assumptions') if run else None})
    reports(client, context)


try:
    with st.container(key="ms-workspace"):
        main()
except APIError as exc:
    error(exc)
    st.info('Check Connection settings in the sidebar, start the backend or correct its address, then select Reconnect / refresh. Your selected context is retained.')
