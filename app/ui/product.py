from urllib.parse import quote
from uuid import uuid4
import streamlit as st
import plotly.graph_objects as go
from app.ui.common import (saved_get, amount, percent, day, chart, line, NO_ACTION, require_run,
                           header, metric, units, status, badge, table, COLORS)


def render(client, context):
    header("Products", "Understand demand, replenishment risk, and replacement costs.")
    if not require_run(context):
        return
    run = context["recommendation"]
    products = {p["sku"]: p for p in run["products"]}
    if not products:
        st.info("This analysis has no product results. Select another saved analysis.")
        return
    if st.session_state.get("product") not in products:
        st.session_state.pop("product", None)
    sku = st.selectbox("Search by product name or SKU", list(products), key="product", format_func=lambda s: f"{products[s]['name']} · {s}")
    p = products[sku]
    inv, price, cost = p["inventory"], p["pricing"], p["costs"]
    st.subheader(p["name"])
    st.caption(f"{sku} · {p['inputs']['category']} · Supplier currency: {cost['currency'] or 'Unavailable'}")
    badge(status(p), "critical" if inv["expedite_required"] else "warning" if inv["order_required"] else "")
    cards = st.columns(4)
    metric(cards[0], "Available stock", units(inv["available_units"]), f"Units at {day(run['origin'])}")
    metric(cards[1], "Replacement cost", amount(cost["replacement_cost_pkr"]), cost["status"].capitalize() + " · per unit")
    metric(cards[2], "Current price", amount(price["current_price_pkr"]), "Tax-exclusive · per unit")
    metric(cards[3], "Suggested price", amount(price["suggested_price_pkr"]), "Saved pricing rules · per unit")
    demand, pricing, details = st.tabs(["Demand & Stock", "Pricing & Costs", "Recommendation Details"])
    with demand:
        st.caption(f"Method: {(p['forecast'].get('method') or 'unavailable').replace('_', ' ')} · Fallback: {p['forecast'].get('fallback_applied')} · Observations: {p['forecast'].get('observation_count')}")
        history = saved_get(client.base_url, f"datasets/{run['dataset_id']}/products/{quote(sku, safe='')}/sales?through={run['origin']}")["rows"]
        future = [r for r in context["forecast_data"]["rows"] if r["sku"] == sku]
        fig = go.Figure()
        line(fig, [r["date"] for r in history], [r["units_sold"] for r in history], "Observed sales", COLORS["blue"])
        line(fig, [r["date"] for r in future], [r["expected_units"] for r in future], "Forecast demand", dash="dash")
        for label, predicate, color in [("Stock-constrained open day", lambda r: r["store_open"] and not r["fully_in_stock"], COLORS["warning"]), ("Closed day", lambda r: not r["store_open"], COLORS["muted"])]:
            flagged = [r for r in history if predicate(r)]
            fig.add_trace(go.Scatter(x=[r["date"] for r in flagged], y=[r["units_sold"] for r in flagged], mode="markers", name=label, marker=dict(color=color, symbol="x", size=6)))
        fig.add_shape(type="line", x0=run["origin"], x1=run["origin"], y0=0, y1=1, yref="paper", line=dict(color=COLORS["muted"], dash="dot"))
        fig.add_annotation(x=run["origin"], y=1.08, yref="paper", text="Forecast origin", showarrow=False, xanchor="right")
        chart(fig, "Observed sales & forecast demand", "Units per day", height=390)
        if not future:
            st.info("Forecast demand is unavailable for this product. Observed sales remain visible.")
        st.caption("Sales are fulfilled observations. Forecasts are estimates; constrained and closed observations are marked separately.")
        daily = inv["daily"]
        if daily:
            fig = go.Figure()
            line(fig, [r["date"] for r in daily], [r["closing_units"] for r in daily], "Closing stock")
            fig.add_bar(x=[r["date"] for r in daily], y=[r["receipt_units"] for r in daily], name="Scheduled receipts", marker_color=COLORS["blue"], opacity=.65)
            line(fig, [r["date"] for r in daily], [r["expected_unmet_units"] for r in daily], "Unmet demand", COLORS["critical"])
            chart(fig, "Inventory projection", "Units")
        else:
            st.info("Inventory projection is unavailable because no usable forecast was saved.")
        st.caption(NO_ACTION)
        table([{"Measure": "Estimated shortage date", "Saved result": day(inv['first_shortage_date'])},
               {"Measure": "Estimated depletion date", "Saved result": day(inv['first_depletion_date'])},
               {"Measure": "Scheduled incoming units", "Saved result": units(inv['scheduled_incoming_units'])},
               {"Measure": "Uncertain overdue units", "Saved result": units(inv['uncertain_overdue_units'])}])
        st.caption("Missing dates are limited to the supplied forecast window. They do not establish that a later shortage cannot occur.")
    with pricing:
        left, right = st.columns(2)
        with left:
            st.subheader("Price & margin")
            table([{"Measure": k, "Saved value": v} for k, v in {
                "Current selling price": amount(price["current_price_pkr"]), "Suggested price": amount(price["suggested_price_pkr"]),
                "Current replacement margin": percent(price["current_replacement_margin"]), "Target margin": percent(price["target_margin"]),
                "Achievable margin at suggested price": percent(price["achievable_margin"]),
                "Price change": amount(price["price_change_pkr"])}.items()])
            st.caption("Replacement margin is gross margin against replacement cost, not net profit.")
        with right:
            st.subheader("Replacement cost inputs")
            components = cost["components"]
            table([{"Measure": k, "Saved value": v} for k, v in {
                "Historical purchase cost": amount(price["historical_unit_cost_pkr"]), "Replacement cost": amount(cost["replacement_cost_pkr"]),
                "Supplier price / unit": amount(components.get("selected_supplier_unit_price"), (cost['currency'] or '') + " "),
                "FX · PKR per currency unit": str(components.get("selected_pkr_per_currency_unit", "Unavailable")),
                "Freight / unit": amount(components.get("selected_freight_unit_pkr")),
                "Additional charges / unit": amount(components.get("other_unit_pkr"))}.items()])
        badge("Cost inputs: " + cost["status"].capitalize(), "warning" if cost["status"] != "available" else "")
        st.caption(f"Quote observed: {day(cost['observed_at'])} · FX observation: {day(cost['rate_date'])} · Quote valid until: {day(cost['components'].get('valid_until'))}")
        if cost["warnings"]:
            st.warning("Cost input flags: " + ", ".join(w.replace("_", " ").capitalize() for w in cost["warnings"]))
        st.caption("Freshness is assessed at the analysis date. An indicative cost may use stale or expired inputs; unavailable cost is never replaced with historical purchase cost. PKR uses an identity FX rate without a dated FX observation.")
    with details:
        st.subheader("Why this recommendation")
        for explanation in p["explanations"]:
            st.write(explanation)
        st.subheader("Timing & quantities")
        table([{"Measure": k, "Saved result": str(v)} for k, v in {
            "Timing status": inv['timing']['status'].replace('_', ' ').capitalize(),
            "Estimated reorder decision date": day(inv['timing'].get('decision_date')),
            "Last evaluated decision date": day(inv['timing'].get('last_evaluated_decision_date')),
            "Coverage quantity": units(inv['order_quantity']), "Quantity status": inv['quantity_status'].replace('_', ' '),
            "Order now": "Yes" if inv['order_required'] else "No", "Lead time (days)": p['lead_time']['effective_lead_days'],
            "Lead-time source": p['lead_time']['method'].replace('_', ' '),
            "MOQ": p['inputs']['moq'], "Pack size": p['inputs']['pack_size'],
            "Uncertain overdue supply (units)": units(inv['uncertain_overdue_units'])}.items()])
        st.caption("Coverage quantity is a candidate, separate from an immediate order. MOQ and pack-size effects are recorded in the explanation above.")
        with st.expander("Assumptions & limitations"):
            for assumption in run['assumptions']:
                st.write(assumption)
        with st.expander("Technical provenance & calculation inputs"):
            st.json(p)
    with st.expander("Local review actions & history"):
        st.caption("Records a local review only. Orders, published prices and inventory are unaffected.")
        token_key = f"action_token_{run['recommendation_run_id']}_{sku}"
        st.session_state.setdefault(token_key, str(uuid4()))
        with st.form("review_action"):
            action = st.radio("Review action", ["Mark reviewed", "Dismiss recommendation"], horizontal=True)
            note = st.text_input("Optional note", max_chars=1000)
            submitted = st.form_submit_button("Save local review")
        signature = (action, note)
        if submitted and st.session_state.get(token_key + "_saved") != signature:
            client.post(f"recommendations/{run['recommendation_run_id']}/actions", {"request_id": st.session_state[token_key], "sku": sku, "action": "reviewed" if action == "Mark reviewed" else "rejected", "note": note})
            st.session_state[token_key + "_saved"] = signature
            st.session_state[token_key] = str(uuid4())
            st.success("Local review saved.")
        actions = [a for a in client.get(f"recommendations/{run['recommendation_run_id']}/actions") if a["sku"] == sku]
        if actions:
            table([{key: a[key] for key in ('timestamp', 'action', 'note')} for a in actions])
        else:
            st.caption("No local review recorded for this product.")

