from datetime import date
from html import escape
import streamlit as st
from app.ui.common import saved_get, saved_download, error, header, table, day
from app.ui.api_client import APIError

STEPS = ['Load data', 'Review validation', 'Prepare forecasting model', 'Generate analysis', 'Open results']


@st.fragment(run_every='2s')
def poll_job(client, job_id):
    try:
        job = client.get(f'jobs/{job_id}')
    except APIError as exc:
        error(exc)
        return
    status = job['status']
    stage = job['stage'].replace('_', ' ').capitalize()
    st.info(f"{status.capitalize()} · {stage}")
    st.caption('Training and validation can take time. This is the saved job status; no estimated completion percentage is shown.')
    if status in ('completed', 'failed'):
        st.session_state['last_job'] = job
        st.session_state.pop('job', None)
        if status == 'completed':
            st.session_state['pending_dataset'] = job['dataset_id']
            st.session_state['setup_step'] = 'Review validation'
            if job.get('model_id'):
                st.session_state['pending_model'] = job['model_id']
                st.session_state['setup_step'] = 'Generate analysis'
        st.rerun()


def load_data(client, busy):
    st.subheader('1 · Load data')
    st.caption('Every import creates a separate dataset. Existing data and saved analyses remain available in Advanced analysis details.')
    source = st.radio('Data source', ['Upload CSV files', 'Synthetic dataset'], key='setup_source', horizontal=True)
    if source == 'Synthetic dataset':
        st.caption('Fictional business records and FX rates. Use Explore demo to reuse an existing complete demonstration.')
        with st.form('demo'):
            with st.expander('Demo generation settings'):
                count = st.number_input('Product count', 1, 200, 20)
                seed = st.number_input('Seed', 0, 2**32 - 1, 42)
                start = st.date_input('History start', date(2025, 10, 16))
                days = st.number_input('History days', 1, 3650, 365)
            submit = st.form_submit_button('Generate demo dataset', type='primary', disabled=busy)
        if submit:
            job = client.post('datasets/demo', {'seed': seed, 'start_date': start.isoformat(), 'days': days, 'sku_count': count})
            st.session_state['job'] = job['job_id']
            st.rerun()
        return
    contracts = saved_get(client.base_url, 'data-contracts')
    st.write('Upload products, daily sales, inventory, supplier quotes, purchase orders, and FX rates together.')
    st.caption('Blank templates are examples of structure, not usable business history. Sales must include every date for every product, including zero sales. Inventory must match the final sales date.')
    st.download_button('Download six blank CSV templates', saved_download(client.base_url, 'data-contracts/templates'),
        file_name='marginshift-csv-templates.zip', mime='application/zip')
    with st.form('upload'):
        columns = st.columns(2)
        uploads = {name: columns[i % 2].file_uploader(name, type='csv', key='upload_' + name) for i, name in enumerate(contracts)}
        submit = st.form_submit_button('Validate and import dataset', type='primary', disabled=busy)
    if submit:
        missing = [name for name, uploaded in uploads.items() if uploaded is None]
        if missing:
            st.error('Choose the missing files: ' + ', '.join(missing))
        else:
            with st.spinner('Validating all six files and importing atomically…'):
                result = client.request('POST', 'datasets/import', files={name.removesuffix('.csv'):
                    (name, f.getvalue(), 'text/csv') for name, f in uploads.items()}).json()
            st.session_state.update(pending_dataset=str(result['dataset_id']), setup_next_step='Review validation')
            st.rerun()
    with st.expander('CSV fields and defaults'):
        for filename, fields in contracts.items():
            st.write(filename)
            st.caption('Required: ' + ', '.join(fields['required_fields']))
            st.caption('Optional (backend defaults): ' + ', '.join(fields['optional_fields']))
        st.caption('Margin fractions use 0.20 for 20%; monetary inputs allow at most two decimals. Empty quotes/orders/rates still need their headers. Default maximum batch: 12 MiB (configured on the API).')


def quality_review(client, dataset_id):
    quality = saved_get(client.base_url, f'datasets/{dataset_id}/summary')
    st.subheader('2 · Review validation')
    st.caption(f"{quality['product_count']} products · {day(quality['date_range']['start'])} – {day(quality['date_range']['end'])} · {quality['manifest']['provenance'].title()} data")
    table([{'Check': 'Sales coverage (product-days)', 'Result': f"{quality['sales_coverage']['actual_sku_days']:,} / {quality['sales_coverage']['expected_sku_days']:,}"},
           {'Check': 'Stock-constrained open product-days', 'Result': str(quality['stockout_affected_sku_days'])},
           {'Check': 'Closed product-days', 'Result': str(quality['closed_store_sku_days'])},
           {'Check': 'Cost inputs', 'Result': 'Available' if quality['readiness']['current_cost_inputs_available'] else 'Some inputs unavailable'}])
    flags = quality['readiness']['cost_blockers'] + quality['warnings']
    if flags:
        table([{'Issue': f['code'].replace('_', ' ').capitalize(), 'Detail': f.get('message') or ', '.join(f.get('items', []))} for f in flags])
    else:
        st.success('Validation complete. No data-quality warnings reported.')
    st.caption('Validation does not guarantee sufficient training history. Freshness is measured at the analysis date.')
    with st.expander('Full quality report and provenance'):
        st.json(quality)
    st.button('Continue to model preparation', type='primary', on_click=lambda: st.session_state.update(setup_step=STEPS[2]))


def train(client, context, busy):
    st.subheader('3 · Prepare forecasting model')
    if context.get('model'):
        st.success('A completed model is selected. You can reuse it without training again.')
        st.button('Continue to analysis', type='primary', on_click=lambda: st.session_state.update(setup_step=STEPS[3]))
    st.caption('Training compares Random Forest with a simple recent-demand baseline. The validation results select the method; the test period remains separate. This can take time.')
    with st.form('train'):
        with st.expander('Advanced training settings'):
            seed = st.number_input('Training seed', 0, 2**32 - 1, 42)
        submitted = st.form_submit_button('Train and evaluate', disabled=busy)
    if submitted:
        job = client.post('models/train', {'dataset_id': context['dataset'], 'seed': seed})
        st.session_state['job'] = job['job_id']
        st.rerun()


def generate_analysis(client, context, busy):
    st.subheader('4 · Generate analysis')
    st.write('Create a demand outlook and save inventory and pricing decisions. Completed forecasts are reused when their dates and horizon match.')
    if not context.get('model'):
        st.info('Prepare a forecasting model first. The completed data import stays saved.')
        return
    with st.form('recommend'):
        horizon = st.number_input('Outlook (days)', 1, 30, 30, key='setup_horizon')
        with st.expander('Advanced analysis settings'):
            st.caption('Settings apply to a new saved analysis. Existing results keep their original settings.')
            step = st.number_input('Price rounding step (PKR)', min_value=.01, value=5.0, step=1.0, key='draft_step', help='Round the target price upward to this increment.')
            cap_enabled = st.checkbox('Limit suggested price changes', key='draft_cap_enabled')
            cap = st.number_input('Maximum price change (%)', min_value=0.0, max_value=10000.0, value=25.0, key='draft_cap', help='A price cap can prevent achieving the target margin.')
        submitted = st.form_submit_button('Generate analysis', type='primary', disabled=busy)
    if submitted:
        origin = context['dataset_row']['end_date']
        pricing = {'price_step_pkr': str(step), 'max_price_change_pct': str(cap) if cap_enabled else None}
        signature = (client.base_url, context['dataset'], context['model'], origin, horizon, tuple(pricing.items()))
        previous = st.session_state.get('draft_submission', {})
        if previous.get('signature') == signature and previous.get('run_id'):
            st.session_state.update(pending_run=previous['run_id'], setup_next_step=STEPS[4])
            st.rerun()
        forecast = context.get('forecast_data')
        if not forecast or forecast['origin'] != origin or forecast['horizon_days'] != horizon:
            with st.spinner('Generating demand outlook…'):
                forecast = client.post('forecasts', {'dataset_id': context['dataset'], 'model_id': context['model'], 'origin': origin, 'horizon_days': horizon})
            # Preserve this successful step even when the following request fails.
            st.session_state['pending_forecast'] = forecast['forecast_run_id']
        with st.spinner('Saving inventory and pricing decisions…'):
            run = client.post('recommendations', {'dataset_id': context['dataset'], 'forecast_run_id': forecast['forecast_run_id'],
                'pricing': pricing})
        st.session_state['draft_submission'] = {'signature': signature, 'run_id': run['recommendation_run_id']}
        st.session_state.update(pending_run=run['recommendation_run_id'], setup_next_step=STEPS[4])
        st.rerun()


def render(client, context):
    header('Data & Settings', 'Load data, review validation, prepare a model, and open a saved analysis.')
    if st.session_state.get('setup_next_step'):
        st.session_state['setup_step'] = st.session_state.pop('setup_next_step')
    demo_request = st.session_state.pop('demo_requested', None)
    if demo_request:
        st.session_state.update(setup_step='Review validation' if demo_request == 'reuse' else 'Load data', setup_source='Synthetic dataset')
        st.info('No compatible complete demo is saved. Prepare it explicitly below; completed steps are kept for reuse.')
    if st.session_state.get('job'):
        poll_job(client, st.session_state['job'])
    last = st.session_state.get('last_job')
    if last and last['status'] == 'failed':
        st.error(last.get('error') or 'Preparation failed.')
        st.caption('Earlier steps are saved. Correct the issue and submit the failed step again. Job reference: ' + last['id'])
    busy = bool(st.session_state.get('job'))
    ds = context.get('dataset')
    complete = [bool(ds), bool(ds), bool(context.get('model')), bool(context.get('recommendation')), bool(context.get('recommendation'))]
    next_step = STEPS[0 if not ds else 2 if not context.get('model') else 3 if not context.get('recommendation') else 4]
    steps = ''.join(f'<span class="rq-badge {"" if done else "pending"}">{index} · {escape(name)} · {"Ready" if done else "Pending"}</span>'
                    for index, (name, done) in enumerate(zip(STEPS[:4], complete), 1))
    st.markdown(f'<div class="ms-steps" aria-label="Setup progress">{steps}</div>', unsafe_allow_html=True)
    st.session_state.setdefault('setup_step', next_step)
    chosen = st.selectbox('Setup step', STEPS, key='setup_step')
    if chosen == STEPS[0]:
        load_data(client, busy)
    elif not ds:
        st.info('Load data first. Choose Load data to upload all six CSV files or generate a synthetic dataset.')
    elif chosen == STEPS[1]:
        quality_review(client, ds)
    elif chosen == STEPS[2]:
        train(client, context, busy)
    elif chosen == STEPS[3]:
        generate_analysis(client, context, busy)
    elif context.get('recommendation'):
        st.success('Your analysis is saved and ready. Open Overview to review priority actions, then download selected reports.')
        st.button('Open Overview', type='primary', on_click=lambda: st.session_state.update(navigate='Overview'))
    else:
        st.info('Generate an analysis first. Completed data, model, and forecast steps remain saved.')
