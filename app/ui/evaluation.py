import plotly.graph_objects as go
import streamlit as st
from app.ui.common import saved_get, percent, day, header, metric, chart, table, COLORS

METHODS = {"baseline": "Baseline", "random_forest": "Random Forest"}


def score_value(value):
    return "Unavailable" if value is None else f"{value:,.2f}"


def render(client, context):
    header("Model Performance", "Compare demand forecasts with the baseline and inspect the evidence behind model selection.")
    if not context.get("model"):
        st.info("Train or select a model in Data & Settings first.")
        return
    model = saved_get(client.base_url, f"models/{context['model']}/metrics")
    details = model["details"]
    if model["status"] != "completed":
        st.info(f"Model status: {model['status']}. Select a completed model or review the training job in Data & Settings.")
        with st.expander("Model status details"):
            st.json({"error": model.get("error"), "details": details})
        return
    selection = details['selection']
    with st.container(border=True):
        method, basis = st.columns([1, 3])
        method.write("Selected method")
        method.write(METHODS.get(model['selected_method'], model['selected_method']))
        reasons = {"lower_validation_mae": "Lower validation MAE", "validation_tie_baseline_preferred": "Validation tie; baseline preferred",
                   "insufficient_comparable_validation_evidence": "Insufficient comparable validation evidence; baseline retained"}
        basis.write("Selection basis")
        basis.write(reasons.get(selection['reason'], selection['reason'].replace('_', ' ')))
        st.caption("The method was selected using 7-day validation results. Test results assess it afterward and do not change that selection.")
    filters = st.columns(2)
    partition = filters[0].selectbox("Evaluation context", ["test", "validation"], format_func=lambda v: "Held-out test" if v == 'test' else "Validation · method selection")
    horizon = filters[1].selectbox("Evaluation horizon (days)", [1, 7, 30], index=1)
    result = details[partition][str(horizon)]
    scores = result['methods']
    base, forest = scores['baseline'], scores['random_forest']
    dates = details['partitions'][partition]
    st.caption(f"{partition.title()} dates: {day(dates['start'])} – {day(dates['end'])} · {horizon}-day recursive forecasts")
    cards = st.columns(4)
    metric(cards[0], "Baseline MAE", score_value(base['mae_units']), "Units per product-day · lower is better")
    metric(cards[1], "Random Forest MAE", score_value(forest['mae_units']), "Units per product-day · lower is better")
    metric(cards[2], "Baseline WAPE", percent(base['wape']), f"Random Forest: {percent(forest['wape'])} · Lower is better")
    metric(cards[3], "Evaluated observations", f"{base['count']:,}", "Paired product-day appearances")
    left, right = st.columns(2)
    for col, field, title, unit in [(left, 'mae_units', 'Average absolute error', 'Units per product-day'),
                                    (right, 'wape', 'Weighted absolute percentage error', 'WAPE (%)')]:
        with col:
            fig = go.Figure()
            for key, color in [('baseline', COLORS['blue']), ('random_forest', COLORS['teal'])]:
                value = scores[key][field]
                fig.add_bar(x=[METHODS[key]], y=[value * 100 if field == 'wape' and value is not None else value],
                            name=METHODS[key], marker_color=color, hovertemplate="%{x}<br>%{y:.2f}<extra></extra>")
            chart(fig, title, unit, xaxis="Method", height=300)
    st.caption("MAE: average error in units per product-day. WAPE: total absolute error relative to total observed demand. Lower is better; neither is an accuracy score. Missing scores are unavailable, not zero.")
    st.subheader("Per-product comparison")
    only_baseline = st.checkbox("Show products where the baseline performed better")
    rows = []
    for sku, methods in result['per_product'].items():
        a, b = methods['baseline'], methods['random_forest']
        baseline_better = a['mae_units'] is not None and b['mae_units'] is not None and a['mae_units'] < b['mae_units']
        if only_baseline and not baseline_better:
            continue
        rows.append({"SKU": sku, "Baseline MAE": a['mae_units'], "RF MAE": b['mae_units'],
                     "Baseline WAPE (%)": a['wape'] * 100 if a['wape'] is not None else None,
                     "RF WAPE (%)": b['wape'] * 100 if b['wape'] is not None else None,
                     "Observations": a['count'], "Baseline better": baseline_better,
                     "Baseline status": a['status'].replace('_', ' '), "RF status": b['status'].replace('_', ' ')})
    if rows:
        table(rows, column_config={**{k: st.column_config.NumberColumn(format="%.2f") for k in ('Baseline MAE', 'RF MAE')},
              **{k: st.column_config.NumberColumn(format="%.1f%%") for k in ('Baseline WAPE (%)', 'RF WAPE (%)')}})
    else:
        st.info("No products match this comparison filter.")
    if horizon == 30:
        cumulative = result['cumulative_demand']
        st.subheader("Thirty-day cumulative coverage")
        st.caption(cumulative['definition'])
        if any(s['status'] != 'ok' for s in cumulative['methods'].values()):
            st.warning(f"Insufficient thirty-day cumulative coverage. At least {cumulative['minimum_complete_windows']} complete windows are required; unavailable scores are not zero error.")
        table([{"Method": METHODS[k], "Complete windows": v['count'], "MAE (units per window)": v['mae_units'],
                "Status": v['status'].replace('_', ' ')} for k, v in cumulative['methods'].items()])
        with st.expander("Cumulative per-product coverage"):
            st.json(cumulative)
    with st.expander("Methodology, split dates & exclusions"):
        table([{"Period": name.title(), "Start": day(bounds['start']), "End": day(bounds['end'])}
               for name, bounds in details['partitions'].items()])
        st.write(details['evaluation_windows'])
        st.write(details['comparison'])
        st.caption("Overlapping windows can count a target date more than once. These are paired appearances, not independent observations. Censored open days, closed days and baseline fallbacks are excluded from paired model scores.")
        table([{"Count": key.replace('_', ' ').capitalize(), "Value": value} for key, value in result['counts'].items()])
        table([{"Method": METHODS[k], "Score status": s['status'], "WAPE status": s['wape_status']}
               for k, s in scores.items()])
        st.caption("Point forecasts have no calibrated confidence bands. Synthetic validation does not establish commercial effectiveness.")
        st.json({"selection": selection, "coverage": details['coverage'],
                 "fallback_reasons": result['fallback_reason_counts_by_sku_origin']})
