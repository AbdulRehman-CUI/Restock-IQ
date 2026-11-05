from decimal import Decimal
import streamlit as st
from app.ui.common import (choose, saved_get, amount, percent, compact_money, number,
                           header, table, money_column, badge, metric)
from app.ui.state import margin_fraction
from app.ui.api_client import APIError
from app.ui.common import error

DRAFT = {"name": "Cost and delivery scenario", "fx": 0.0, "supplier": 0.0, "freight": 0.0, "lead": 0, "override": False, "margin": 20.0}
PRESETS = ['Custom assumptions', 'FX increase only', 'Delivery delay only', 'FX increase plus delivery delay']


def preset(kind):
    for key, value in DRAFT.items():
        st.session_state["scenario_" + key] = value
    if kind in ('FX increase only', 'FX increase plus delivery delay'):
        st.session_state['scenario_fx'] = 10.0
    if kind in ('Delivery delay only', 'FX increase plus delivery delay'):
        st.session_state['scenario_lead'] = 7
    if kind == "Reset":
        st.session_state["scenario_preset"] = "Custom assumptions"


def draft_assumptions():
    return {"fx_change_pct": str(st.session_state['scenario_fx']),
            "supplier_price_change_pct": str(st.session_state['scenario_supplier']),
            "additional_freight_pkr": str(st.session_state['scenario_freight']),
            "extra_lead_days": st.session_state['scenario_lead'],
            "target_margin_override": margin_fraction(st.session_state['scenario_margin'], st.session_state['scenario_override'])}


def unapplied(draft, saved):
    return any((None if draft[k] is None else Decimal(str(draft[k]))) !=
               (None if saved[k] is None else Decimal(str(saved[k]))) for k in draft)


def comparison_changed(pair):
    if pair['base'] is None or pair['scenario'] is None:
        return (pair['base'] is None) != (pair['scenario'] is None)
    return Decimal(str(pair['base'])) != Decimal(str(pair['scenario']))


def assumptions_caption(a):
    return (f"FX {Decimal(str(a['fx_change_pct'])):+g}% · Supplier {Decimal(str(a['supplier_price_change_pct'])):+g}% · "
            f"Freight +{amount(a['additional_freight_pkr'])}/unit · Lead +{a['extra_lead_days']} days · "
            f"Target margin {percent(a['target_margin_override']) if a['target_margin_override'] is not None else 'product defaults'}")


def calculate(client, context, base, scenario_id):
    return client.post('recommendations', {'dataset_id': context['dataset'], 'forecast_run_id': context['forecast'],
        'scenario_id': scenario_id, 'pricing': base['effective_settings']['pricing']})


def render_results(client, context, base, result_id):
    st.subheader("Scenario results")
    if not result_id:
        st.info("Set assumptions and select Run scenario, or choose a saved scenario with compatible results.")
        st.caption("Results will compare against the selected saved base analysis. Changing controls never starts a calculation.")
        return
    scenario_run = saved_get(client.base_url, f"recommendations/{result_id}/metadata")
    if scenario_run["forecast_run_id"] != context["forecast"] or scenario_run["effective_settings"] != base["effective_settings"]:
        st.info("Select a scenario result with the same forecast and pricing settings as the base analysis.")
        return
    if st.session_state.get('_motion_result') != result_id:
        st.session_state['_motion_result'] = result_id
        st.markdown('<div class="ms-result-update" role="status">Scenario comparison ready · saved results</div>', unsafe_allow_html=True)
    saved = scenario_run["scenario"]
    badge("Hypothetical scenario")
    st.write(saved["name"])
    st.caption("Calculated assumptions · " + assumptions_caption(saved['assumptions']))
    # Forms batch client edits until submission. Always identify the immutable result snapshot.
    st.caption("Saved results below use only these calculated assumptions. Form edits are unapplied changes until you select Run scenario.")
    if unapplied(draft_assumptions(), saved['assumptions']) or st.session_state['scenario_name'] != saved['name']:
        st.warning("Unapplied changes · the draft differs from these saved results.")
    base_id = base["recommendation_run_id"]
    comparison = saved_get(client.base_url, f"recommendations/{base_id}/compare/{result_id}")
    workspaces = [saved_get(client.base_url, f"recommendations/{id}/workspace") for id in (base_id, result_id)]
    summaries = [w['summary'] for w in workspaces]
    cards = st.columns(3)
    for col, label, key in zip(cards[:2], ["Order now · scenario", "Expedite supply · scenario"], ['order_now_products', 'expedite_products']):
        a, b = (s[key] for s in summaries)
        metric(col, label, b, f"Base {a} → Scenario {b}", delta=f"{b-a:+d} products", delta_color="inverse")
    shared_cash = comparison['aggregate']['estimated_replenishment_cash_pkr']
    cash_delta = shared_cash['delta']
    metric(cards[2], "Coverage cash · scenario", compact_money(shared_cash['scenario']),
        f"Base: {amount(shared_cash['base'])} · Scenario: {amount(shared_cash['scenario'])} · All common calculable coverage candidates",
        delta=None if cash_delta is None else amount(cash_delta), delta_color="inverse")
    st.caption(f"Coverage cash: {'Partial · ' if shared_cash['partial'] else ''}{shared_cash['included_products']} common products / {shared_cash['excluded_products']} excluded. It includes coverage candidates beyond orders required now.")
    st.subheader("Base & scenario")
    table([{"Measure": label, "Base": amount(summaries[0][key]['value']) if key == 'order_now_cash' else str(summaries[0][key]),
            "Scenario": amount(summaries[1][key]['value']) if key == 'order_now_cash' else str(summaries[1][key])}
           for label, key in [("Order now (products)", "order_now_products"), ("Expedite supply (products)", "expedite_products"),
                              ("Projected shortages (products)", "shortage_products"), ("Order-now replenishment cash", "order_now_cash")]])
    for label, summary in zip(["Base", "Scenario"], summaries):
        cash = summary["order_now_cash"]
        st.caption(f"{label} order-now cash: {'Partial · ' if cash['partial'] else ''}{cash['included_products']} included / {cash['excluded_products']} excluded. Each total uses that run's order-now products.")
    scenario_products = {p['sku']: p for p in workspaces[1]['products']}
    base_products = {p['sku']: p for p in workspaces[0]['products']}
    view = st.selectbox("Product comparison", ["Pricing & margin", "Stock & replenishment"])
    rows = []
    affected_only = st.checkbox('Show affected products only', value=True)
    affected_count = 0
    for p in comparison["products"]:
        changed = any(comparison_changed(p[key]) for key in
            ('replacement_cost_pkr', 'suggested_price_pkr', 'current_replacement_margin', 'expected_unmet_units', 'order_quantity', 'estimated_replenishment_cash_pkr')) or p['first_shortage_date']['base'] != p['first_shortage_date']['scenario']
        changed = changed or any(base_products[p['sku']]['inventory'][key] != scenario_products[p['sku']]['inventory'][key]
            for key in ('order_required', 'expedite_required'))
        affected_count += bool(changed)
        if affected_only and not changed:
            continue
        sku = p['sku']
        row = {"Product": p['name'], "SKU": sku}
        if view == "Pricing & margin":
            row.update({"Base cost": number(p['replacement_cost_pkr']['base']), "Scenario cost": number(p['replacement_cost_pkr']['scenario']),
                        "Cost difference": number(p['replacement_cost_pkr']['delta']),
                        "Base price": number(p['suggested_price_pkr']['base']), "Scenario price": number(p['suggested_price_pkr']['scenario']),
                        "Margin difference (pp)": number(Decimal(str(p['current_replacement_margin']['delta'])) * 100) if p['current_replacement_margin']['delta'] is not None else None})
        else:
            row.update({"Base order now": base_products[sku]['inventory']['order_required'],
                        "Scenario order now": scenario_products[sku]['inventory']['order_required'],
                        "Base coverage units": p['order_quantity']['base'], "Scenario coverage units": p['order_quantity']['scenario'],
                        "Unmet demand difference (units)": number(p['expected_unmet_units']['delta']),
                        "Shortage shift (days)": p['first_shortage_date']['shift_days']})
        rows.append(row)
    st.caption(f'{affected_count} of {len(comparison["products"])} products have changed results, availability, or supply urgency. Unavailable differences are not treated as zero.')
    table(rows, column_config={**{key: money_column(key) for key in ('Base cost', 'Scenario cost', 'Cost difference', 'Base price', 'Scenario price')},
        "Margin difference (pp)": st.column_config.NumberColumn(format="%.1f pp"),
        "Unmet demand difference (units)": st.column_config.NumberColumn(format="%.1f")})
    st.caption("Differences are scenario minus base. Margin differences are percentage points (pp). Missing comparisons remain unavailable. A positive shortage-date shift is a later date, within the forecast only.")
    with st.expander("Full comparison & technical details"):
        st.json(comparison)
        st.json({"scenario": saved, "base_run_id": base_id, "scenario_run_id": result_id})


def render(client, context):
    header("What-if Scenarios", "Explore how cost and delivery changes affect your decisions.")
    st.caption("Demand is held unchanged; customer response to price changes is not modeled.")
    if not context.get("forecast"):
        st.info("Select or create a forecast in Data & Settings first.")
        return
    bases = [r for r in context["runs"] if not r.get("scenario_id")]
    active = context.get("recommendation")
    if "base_run" not in st.session_state and active and not active["scenario_id"]:
        st.session_state["base_run"] = active["recommendation_run_id"]
    with st.expander("Saved analyses & scenarios", expanded=not bool(st.session_state.get('scenario_result'))):
        selection = st.columns(2)
        with selection[0]:
            base_id = choose("Compare against saved base", bases, "base_run")
        if not base_id:
            st.info("Generate base recommendations in Data & Settings first.")
            return
        base = saved_get(client.base_url, f"recommendations/{base_id}/metadata")
        scenarios = client.get("scenarios", dataset_id=context["dataset"], forecast_run_id=context["forecast"])
        active_id = active['recommendation_run_id'] if active else None
        if active and active.get('scenario_id') and st.session_state.get('scenario_active_context') != active_id:
            # Honor an exact scenario run chosen in the shared analysis selector.
            st.session_state['saved_scenario'] = active['scenario_id']
            st.session_state['scenario_result'] = active_id
            st.session_state['scenario_selected_before'] = (base_id, active['scenario_id'])
        st.session_state['scenario_active_context'] = active_id
        pending = st.session_state.pop("scenario_pending_selection", None)
        if pending and any(s['id'] == pending for s in scenarios):
            st.session_state['saved_scenario'] = pending
        with selection[1]:
            selected = choose("Saved scenario", scenarios, "saved_scenario")
            if not scenarios:
                st.caption("No saved scenarios for this forecast.")
        selection_signature = (base_id, selected)
        if st.session_state.get("scenario_selected_before") != selection_signature:
            st.session_state.pop("scenario_result", None)
            st.session_state["scenario_selected_before"] = selection_signature
        if selected:
            existing = saved_get(client.base_url, f"scenarios/{selected}")
            st.caption(existing['name'] + " · " + assumptions_caption(existing['assumptions']))
            if st.button("Use saved assumptions as draft"):
                a = existing["assumptions"]
                st.session_state.update(scenario_name=existing["name"], scenario_fx=float(a["fx_change_pct"]),
                    scenario_supplier=float(a["supplier_price_change_pct"]), scenario_freight=float(a["additional_freight_pkr"]),
                    scenario_lead=a["extra_lead_days"], scenario_override=a["target_margin_override"] is not None,
                    scenario_margin=float(a["target_margin_override"] if a["target_margin_override"] is not None else .2) * 100)
                st.rerun()
    candidate_runs = [r for r in context["runs"] if r.get("scenario_id") and r["scenario_id"] == selected and
        saved_get(client.base_url, f"recommendations/{r['id']}/metadata")["effective_settings"] == base["effective_settings"]]
    if selected and not candidate_runs:
        st.caption("This saved scenario has no result compatible with the selected base settings.")
        if st.button("Calculate recommendations for saved scenario"):
            with st.spinner("Calculating saved scenario…"):
                result = calculate(client, context, base, selected)
            st.session_state["scenario_result"] = result["recommendation_run_id"]
            st.rerun()
    for key, value in DRAFT.items():
        st.session_state.setdefault("scenario_" + key, value)
    assumptions, results = st.columns([1, 1.5], gap="large")
    with assumptions:
        st.subheader("Assumptions")
        kind = st.selectbox("Preset", PRESETS, key="scenario_preset", on_change=lambda: preset(st.session_state['scenario_preset']))
        st.button("Reset assumptions", on_click=preset, args=("Reset",))
        with st.form("scenario_form"):
            name = st.text_input("Scenario name", key="scenario_name", max_chars=200)
            st.subheader("Cost assumptions")
            fx = st.number_input("FX change (%)", min_value=-99.9999, max_value=10000.0, step=1.0, format="%.4f", key="scenario_fx")
            supplier = st.number_input("Supplier-price change (%)", min_value=-99.9999, max_value=10000.0, step=1.0, format="%.4f", key="scenario_supplier")
            freight = st.number_input("Additional freight / unit (PKR)", min_value=0.0, max_value=100000000000.0, step=1.0, key="scenario_freight")
            st.subheader("Delivery changes")
            lead = st.number_input("Delivery delay (days)", min_value=0, max_value=3650, key="scenario_lead")
            st.caption('Shifts expected open-order arrivals and the delivery time for a prospective new order. Already overdue supply remains uncertain.')
            st.subheader("Optional pricing settings")
            enabled = st.checkbox("Override target margin", key="scenario_override")
            margin = st.number_input("Target-margin override (%)", min_value=0.0, max_value=80.0, step=.1, key="scenario_margin")
            st.caption('Assumption summary · ' + assumptions_caption(draft_assumptions()))
            st.caption("Unapplied changes · edits here affect results only after submission. Saved base pricing settings are reused exactly.")
            submitted = st.form_submit_button("Run scenario", type="primary", use_container_width=True)
        if submitted:
            body = {"dataset_id": context["dataset"], "forecast_run_id": context["forecast"], "name": name, **draft_assumptions()}
            signature = (base_id, name, tuple(draft_assumptions().items()))
            previous = st.session_state.get('scenario_submission', {})
            try:
                st.info("Calculating submitted assumptions. Any previous comparison remains a saved result until this request completes.")
                with st.spinner("Saving scenario & calculating decisions…"):
                    if previous.get('signature') == signature:
                        scenario_id = previous['scenario_id']
                    else:
                        scenario_id = client.post('scenarios', body)['id']
                        previous = {'signature': signature, 'scenario_id': scenario_id}
                        st.session_state['scenario_submission'] = previous
                    st.session_state['scenario_created'] = scenario_id
                    st.session_state['scenario_pending_selection'] = scenario_id
                    st.session_state.pop('scenario_result', None)
                    result_id = previous.get('result_id')
                    if not result_id:
                        result_id = calculate(client, context, base, scenario_id)['recommendation_run_id']
                        st.session_state['scenario_submission'] = {**previous, 'result_id': result_id}
                st.session_state.update(scenario_result=result_id, scenario_selected_before=(base_id, scenario_id))
                st.session_state.pop('scenario_created', None)
                st.rerun()
            except APIError as exc:
                error(exc)
        if st.session_state.get('scenario_created') and not st.session_state.get('scenario_result'):
            st.info('Your scenario assumptions are saved. Refresh discovery to check for completed results after a timeout. Retry calculation below reuses those assumptions.')
            if st.button('Retry saved scenario calculation'):
                result = calculate(client, context, base, st.session_state['scenario_created'])
                st.session_state['scenario_result'] = result['recommendation_run_id']
                if st.session_state.get('scenario_submission'):
                    st.session_state['scenario_submission'] = {**st.session_state['scenario_submission'], 'result_id': result['recommendation_run_id']}
                st.session_state.pop('scenario_created', None)
                st.rerun()
    with results:
        result_id = st.session_state.get("scenario_result") or (candidate_runs[0]["id"] if candidate_runs else None)
        if st.session_state.get('scenario_created'):
            result_id = None
        render_results(client, context, base, result_id)
