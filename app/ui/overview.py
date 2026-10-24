import pandas as pd
import streamlit as st
from app.ui.common import (saved_get, amount, compact_money, number, NO_ACTION, require_run,
                           header, metric, status, table, money_column)


def open_product(sku):
    st.session_state["product"] = sku
    st.session_state["navigate"] = "Products"


def render(client, context):
    header("Overview", "Inventory and margin decisions for the selected analysis.")
    if not require_run(context):
        return
    run = context["recommendation"]
    summary = saved_get(client.base_url, f"recommendations/{run['recommendation_run_id']}/overview")
    cash = summary["order_now_cash"]
    cards = st.columns(4)
    metric(cards[0], "Order now", summary["order_now_products"], "Products flagged for an immediate order")
    metric(cards[1], "Expedite supply", summary["expedite_products"], "Shortage before a new order can arrive")
    metric(cards[2], "Below target margin", summary["below_target_margin_products"], "Current price versus replacement cost")
    metric(cards[3], "Order-now replenishment cash", compact_money(cash["value"]),
           f"{'Partial · ' if cash['partial'] else ''}{amount(cash['value'])} · {cash['included_products']} included / {cash['excluded_products']} excluded")
    st.subheader("Priority actions")
    products = sorted(run["products"], key=lambda p: (not p["inventory"]["expedite_required"],
        not p["inventory"]["order_required"], p["inventory"]["first_shortage_date"] or "9999", p["sku"]))
    urgent = [p for p in products if p["inventory"]["supply_action_required"] or
              "TARGET_MARGIN_UNMET" in p["reason_codes"] or p["costs"]["status"] == "unavailable"][:4]
    if not urgent:
        st.info("No immediate actions in the selected results. Review the supported forecast window and data availability below.")
    for p in urgent:
        with st.container(border=True):
            name, reason, action = st.columns([2, 4, 1])
            name.write(p["name"])
            name.caption(f"{p['sku']} · {status(p)}")
            # Display persisted explanations, never new business recommendations.
            explanations = p["explanations"]
            prefix = ("Demand is expected" if p['inventory']['expedite_required'] else
                      "Quantity status" if p['inventory']['order_required'] else
                      "Replacement cost" if p['costs']['status'] == 'unavailable' else "Price PKR")
            relevant = next((s for s in explanations if s.startswith(prefix)), explanations[0])
            reason.caption(relevant)
            action.button("View details", key="priority_" + p["sku"], on_click=open_product, args=(p["sku"],))
    st.subheader("Inventory decisions")
    filters = st.columns([2, 1, 1])
    search = filters[0].text_input("Search products", placeholder="Product name or SKU")
    category = filters[1].selectbox("Category", ["All"] + sorted({p["inputs"]["category"] for p in products}))
    urgency = filters[2].selectbox("Urgency", ["All", "Order now", "Expedite supply", "Later / no trigger"])
    with st.expander("Additional filters & columns"):
        extra = st.columns(2)
        currency = extra[0].selectbox("Supplier currency", ["All"] + sorted({p["costs"]["currency"] for p in products if p["costs"]["currency"]}))
        missing = extra[1].selectbox("Data availability", ["All", "Needs attention", "Available"])
        optional = st.multiselect("Additional columns", ["Category", "Current price", "Replacement cost", "Suggested price", "Order-now quantity", "Data status"])
    rows = []
    for p in products:
        inv, price, cost = p["inventory"], p["pricing"], p["costs"]
        unavailable = cost["status"] != "available" or inv["quantity_status"] != "available"
        if search.casefold() not in (p["name"] + " " + p["sku"]).casefold():
            continue
        if category != "All" and p["inputs"]["category"] != category or currency != "All" and cost["currency"] != currency:
            continue
        if urgency == "Order now" and not inv["order_required"] or urgency == "Expedite supply" and not inv["expedite_required"] or urgency == "Later / no trigger" and (inv["order_required"] or inv["expedite_required"]):
            continue
        if missing == "Needs attention" and not unavailable or missing == "Available" and unavailable:
            continue
        row = {"Product": p["name"], "SKU": p["sku"], "Status": status(p), "Available stock": number(inv["available_units"]),
            "Coverage quantity": inv["order_quantity"], "Estimated shortage date": pd.to_datetime(inv["first_shortage_date"]),
            "Replacement margin (%)": number(price["current_replacement_margin"]) * 100 if price["current_replacement_margin"] is not None else None}
        extras = {"Category": p["inputs"]["category"], "Current price": number(price["current_price_pkr"]),
            "Replacement cost": number(cost["replacement_cost_pkr"]), "Suggested price": number(price["suggested_price_pkr"]),
            "Order-now quantity": inv["order_quantity"] if inv["order_required"] else None,
            "Data status": f"Cost: {cost['status']} · Quantity: {inv['quantity_status'].replace('_', ' ')}"}
        row.update({key: extras[key] for key in optional})
        rows.append(row)
    st.caption(f"{len(rows)} of {len(products)} products · Priority order: expedite, order now, earliest shortage, SKU.")
    if rows:
        table(rows, column_config={
            "Available stock": st.column_config.NumberColumn(format="%.1f"),
            "Coverage quantity": st.column_config.NumberColumn(format="%d"),
            "Replacement margin (%)": st.column_config.NumberColumn("Replacement margin", format="%.1f%%"),
            "Estimated shortage date": st.column_config.DateColumn(format="DD MMM YYYY"),
            **{key: money_column(key) for key in ("Current price", "Replacement cost", "Suggested price")}})
        controls = st.columns([4, 1])
        selected = controls[0].selectbox("Product details", [r["SKU"] for r in rows],
            format_func=lambda sku: next(r["Product"] + " · " + sku for r in rows if r["SKU"] == sku))
        with controls[1]:
            st.write("")
            st.button("Inspect product", type="primary", on_click=open_product, args=(selected,))
    else:
        st.info("No products match these filters. Clear the search or choose All to broaden your results.")
    st.caption(NO_ACTION)
    st.caption("Coverage quantity is a planning candidate; act now only for products flagged Order now. A missing shortage date means no date is available within this forecast, not a guarantee of future availability. Missing values appear as a dash.")
    st.caption(f"{summary['shortage_products']} products with projected shortages · {summary['overdue_supply_products']} with uncertain overdue supply. Cash scope: {cash['scope']}")
