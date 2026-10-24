from decimal import Decimal
import streamlit as st
from app.ui.common import (choose, saved_get, amount, percent, compact_money, number,
                           header, table, money_column, badge)
from app.ui.state import margin_fraction

DRAFT = {"name": "Cost and delivery scenario", "fx": 0.0, "supplier": 0.0, "freight": 0.0, "lead": 0, "override": False, "margin": 20.0}
PRESETS = ["Custom assumptions", "FX +10% / lead +7", "Phase 3 demonstration"]


def preset(kind):
    for key, value in DRAFT.items():
        st.session_state["scenario_" + key] = value
    if kind not in ("Reset", "Custom assumptions"):
        st.session_state.update(scenario_fx=10.0, scenario_lead=7)
    if kind == "Phase 3 demonstration":
        st.session_state["scenario_override"] = True
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


def assumptions_caption(a):
    return (f"FX {Decimal(str(a['fx_change_pct'])):+g}% · Supplier {Decimal(str(a['supplier_price_change_pct'])):+g}% · "
            f"Freight +{amount(a['additional_freight_pkr'])}/unit · Lead +{a['extra_lead_days']} days · "
            f"Target margin {percent(a['target_margin_override']) if a['target_margin_override'] is not None else 'product defaults'}")


def render_results(client, context, base, result_id):
    st.subheader("Scenario results")
    if not result_id:
        st.info("Set assumptions and select Run scenario, or choose a saved scenario with compatible results.")
        st.caption("Results will compare against the selected saved base analysis. Changing controls never starts a calculation.")
        return
    scenario_run = saved_get(client.base_url, f"recommendations/{result_id}")
    if scenario_run["forecast_run_id"] != context["forecast"] or scenario_run["effective_settings"] != base["effective_settings"]:
        st.info("Select a scenario result with the same forecast and pricing settings as the base analysis.")
        return
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
    summaries = [saved_get(client.base_url, f"recommendations/{id}/overview") for id in (base_id, result_id)]
    cards = st.columns(3)
    for col, label, key in zip(cards[:2], ["Order now · scenario", "Expedite supply · scenario"], ['order_now_products', 'expedite_products']):
        a, b = (s[key] for s in summaries)
        col.metric(label, b, delta=f"{b-a:+d} products", delta_color="inverse")
        col.caption(f"Base {a} → Scenario {b}")
    shared_cash = comparison['aggregate']['estimated_replenishment_cash_pkr']
    cash_delta = shared_cash['delta']
    cards[2].metric("Coverage cash · scenario", compact_money(shared_cash['scenario']),
        delta=None if cash_delta is None else amount(cash_delta), delta_color="inverse")
    cards[2].caption(f"Base: {amount(shared_cash['base'])} · Scenario: {amount(shared_cash['scenario'])} · All common calculable coverage candidates")
    st.caption(f"Coverage cash: {'Partial · ' if shared_cash['partial'] else ''}{shared_cash['included_products']} common products / {shared_cash['excluded_products']} excluded. It includes coverage candidates beyond orders required now.")
    st.subheader("Base & scenario")
    table([{"Measure": label, "Base": amount(summaries[0][key]['value']) if key == 'order_now_cash' else str(summaries[0][key]),
            "Scenario": amount(summaries[1][key]['value']) if key == 'order_now_cash' else str(summaries[1][key])}
           for label, key in [("Order now (products)", "order_now_products"), ("Expedite supply (products)", "expedite_products"),
                              ("Projected shortages (products)", "shortage_products"), ("Order-now replenishment cash", "order_now_cash")]])
    for label, summary in zip(["Base", "Scenario"], summaries):
        cash = summary["order_now_cash"]
        st.caption(f"{label} order-now cash: {'Partial · ' if cash['partial'] else ''}{cash['included_products']} included / {cash['excluded_products']} excluded. Each total uses that run's order-now products.")
    scenario_products = {p["sku"]: p for p in scenario_run["products"]}
    base_products = {p["sku"]: p for p in base["products"]}
    view = st.selectbox("Product comparison", ["Pricing & margin", "Stock & replenishment"])
    rows = []
    for p in comparison["products"]:
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
    table(rows, column_config={**{key: money_column(key) for key in ('Base cost', 'Scenario cost', 'Cost difference', 'Base price', 'Scenario price')},
        "Margin difference (pp)": st.column_config.NumberColumn(format="%.1f pp"),
        "Unmet demand difference (units)": st.column_config.NumberColumn(format="%.1f")})
    st.caption("Differences are scenario minus base. Margin differences are percentage points (pp). Missing comparisons remain unavailable. A positive shortage-date shift is a later date, within the forecast only.")
    with st.expander("Full comparison & technical details"):
        st.json(comparison)
        st.json({"scenario": saved, "base_run_id": base_id, "scenario_run_id": result_id})


def render(client, context):
    header("Scenarios", "Explore how cost and delivery changes affect your decisions.")
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
        base = saved_get(client.base_url, f"recommendations/{base_id}")
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
        saved_get(client.base_url, f"recommendations/{r['id']}")["effective_settings"] == base["effective_settings"]]
    if selected and not candidate_runs:
        st.caption("This saved scenario has no result compatible with the selected base settings.")
        if st.button("Calculate recommendations for saved scenario"):
            with st.spinner("Calculating saved scenario…"):
                result = client.post("recommendations", {"dataset_id": context["dataset"], "forecast_run_id": context["forecast"],
                    "scenario_id": selected, "pricing": base["effective_settings"]["pricing"]})
            st.session_state["scenario_result"] = result["recommendation_run_id"]
            st.rerun()
    for key, value in DRAFT.items():
        st.session_state.setdefault("scenario_" + key, value)
    assumptions, results = st.columns([1, 2], gap="large")
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
            st.subheader("Supply assumptions")
            lead = st.number_input("Additional lead time (days)", min_value=0, max_value=3650, key="scenario_lead")
            st.subheader("Pricing assumptions")
            enabled = st.checkbox("Override target margin", key="scenario_override")
            margin = st.number_input("Target-margin override (%)", min_value=0.0, max_value=80.0, step=.1, key="scenario_margin")
            st.caption("Unapplied changes · edits here affect results only after submission. Saved base pricing settings are reused exactly.")
            submitted = st.form_submit_button("Run scenario", type="primary", use_container_width=True)
        if submitted:
            body = {"dataset_id": context["dataset"], "forecast_run_id": context["forecast"], "name": name, **draft_assumptions()}
            with st.spinner("Saving scenario & calculating decisions…"):
                scenario = client.post("scenarios", body)
                # Retain the immutable ID if calculation fails; recovery never resubmits it automatically.
                st.session_state["scenario_created"] = scenario["id"]
                result = client.post("recommendations", {"dataset_id": context["dataset"], "forecast_run_id": context["forecast"],
                    "scenario_id": scenario["id"], "pricing": base["effective_settings"]["pricing"]})
            st.session_state["scenario_result"] = result["recommendation_run_id"]
            st.session_state['scenario_pending_selection'] = scenario['id']
            st.session_state['scenario_selected_before'] = (base_id, scenario['id'])
            st.success("Scenario and analysis saved.")
        if st.session_state.get('scenario_created') and not st.session_state.get('scenario_result'):
            st.caption("A scenario was saved. Refresh discovery and select it above to recover or calculate its results.")
    with results:
        result_id = st.session_state.get("scenario_result") or (candidate_runs[0]["id"] if candidate_runs else None)
        render_results(client, context, base, result_id)
