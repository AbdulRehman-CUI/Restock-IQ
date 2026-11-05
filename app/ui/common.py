from datetime import date
from decimal import Decimal
from html import escape
from pathlib import Path
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from app.ui.api_client import APIClient, APIError

NO_ACTION = "Projected shortages assume no additional recommended orders are placed."
COLORS = dict(background="#F5F7FA", surface="#FFFFFF", text="#152238", muted="#64748B",
              border="#E2E8F0", teal="#0F766E", hover="#115E59", subtle="#ECFDF5",
              warning="#B45309", warning_bg="#FFFBEB", critical="#B91C1C",
              critical_bg="#FEF2F2", blue="#2563EB", cyan="#0891B2")


def theme():
    """Local CSS uses authored classes and documented container key classes."""
    tokens = ";".join(f"--ms-{key.replace('_', '-')}: {value}" for key, value in COLORS.items())
    css = Path(__file__).with_name("styles.css").read_text(encoding="utf-8")
    st.markdown(f"<style>:root {{{tokens}}}\n{css}</style>", unsafe_allow_html=True)
    st.session_state['_motion_card_index'] = 0


def first_entrance(key):
    seen = st.session_state.setdefault('_motion_seen', [])
    if key in seen:
        return False
    st.session_state['_motion_seen'] = [*seen, key]
    return True


def brand():
    entrance = " ms-brand-enter" if first_entrance('brand') else ""
    st.markdown(f'<div class="rq-brand{entrance}">Margin<span>Shift</span><i aria-hidden="true"></i></div>', unsafe_allow_html=True)



def badge(text, tone=""):
    st.markdown(f'<span class="rq-badge {escape(tone, quote=True)}">{escape(str(text))}</span>', unsafe_allow_html=True)


def header(title, description):
    changed = st.session_state.get('_motion_page') != title
    st.session_state['_motion_page'] = title
    key = 'ms-heading-overview' if title == 'Overview' else 'ms-heading-page'
    motion = 'ms-page-in var(--ms-page-duration) ease-out both' if changed else 'none'
    st.markdown(f'<style>.st-key-{key} {{animation:{motion}}}</style>', unsafe_allow_html=True)
    with st.container(key=key):
        st.markdown('<p class="ms-eyebrow">DECISION WORKSPACE</p>', unsafe_allow_html=True)
        st.title(title)
        st.caption(description)


def metric(column, label, value, caption, **kwargs):
    index = st.session_state.get('_motion_card_index', 0)
    st.session_state['_motion_card_index'] = index + 1
    key = f"ms-card-{index}"
    animate = first_entrance(f"card:{st.session_state.get('_motion_page')}:{label}")
    motion = f"ms-card-in var(--ms-card-duration) {min(index, 3) * 55}ms both" if animate else "none"
    st.markdown(f'<style>.st-key-{key} {{animation:{motion}}}</style>', unsafe_allow_html=True)
    with column.container(key=key, border=True):
        st.metric(label, value, **kwargs)
        st.caption(caption)



def compact_money(value):
    if value is None:
        return "Unavailable"
    number = Decimal(str(value))
    for divisor, suffix in ((Decimal('1000000000'), 'B'), (Decimal('1000000'), 'M'), (Decimal('1000'), 'K')):
        if abs(number) >= divisor:
            return f"PKR {number / divisor:,.2f}{suffix}"
    return amount(value)


def number(value):
    return None if value is None else float(value)


def units(value):
    return "Unavailable" if value is None else f"{Decimal(str(value)):,.1f}"


def status(product):
    inv = product["inventory"]
    if inv["expedite_required"]:
        return "Expedite supply"
    if inv["order_required"]:
        return "Order now"
    if inv["status"] == "unavailable":
        return "Forecast unavailable"
    if inv["quantity_status"] != "available":
        return "Limited forecast horizon"
    return {"estimated_future_decision": "Plan ahead", "no_trigger_within_supported_window": "No trigger in window"}.get(
        inv["timing"]["status"], inv["timing"]["status"].replace("_", " ").capitalize())


def table(rows, **kwargs):
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, **kwargs)


def money_column(label):
    return st.column_config.NumberColumn(label, format="PKR %.2f")


def amount(value, prefix="PKR "):
    return "Unavailable" if value is None else f"{prefix}{Decimal(str(value)):,.2f}"


def percent(value):
    return "Unavailable" if value is None else f"{Decimal(str(value)) * 100:.1f}%"


def day(value):
    return "Unavailable" if not value else date.fromisoformat(str(value)[:10]).strftime("%d %b %Y")


@st.cache_data(show_spinner=False, max_entries=256)
def saved_get(base_url, path):
    return APIClient(base_url).get(path)


@st.cache_data(show_spinner=False, max_entries=64)
def saved_download(base_url, path):
    return APIClient(base_url).download(path)


def error(exc):
    st.error(str(exc))
    if exc.payload.get("errors"):
        corrections = {
            'missing': 'Supply the required value / column.',
            'missing_column': 'Add the required column using the downloadable template header.',
            'missing_file': 'Include this CSV with its header in the six-file batch.',
            'unknown_column': 'Remove this column; use the downloadable contract headers.',
            'duplicate_record': 'Keep one record per required key.',
            'missing_sales_day': 'Include every product and calendar date; record zero sales explicitly.',
            'missing_snapshot': 'Add inventory for this product on the final sales date.',
            'snapshot_origin_mismatch': 'Use the final sales date for every inventory snapshot.',
            'unknown_product': 'Add this SKU to products.csv or correct the reference.',
        }
        table([{**row, 'Suggested correction': corrections.get(row.get('code'),
            'Correct the value using the message and CSV contract, then resubmit all six files.')} for row in exc.payload['errors']])
    if exc.payload:
        with st.expander("Detailed error report"):
            st.json(exc.payload)
    st.caption("Completed steps remain saved. After a timeout, select Reconnect / refresh to check for a saved result before submitting again.")


def choose(label, rows, key, formatter=None):
    options = [r["id"] for r in rows]
    lookup = {r["id"]: r for r in rows}
    if st.session_state.get(key) not in options:
        st.session_state.pop(key, None)
    if not rows:
        return None
    return st.selectbox(label, options, key=key, format_func=lambda id: (formatter or run_label)(lookup[id]))


def run_label(row):
    context = row.get("name") or ("Scenario" if row.get("scenario_id") else row.get("selected_method") or "Base")
    when = row.get("origin") or row.get("training_cutoff") or row.get("created_at", "")[:10]
    if not row.get('name'):
        context = context.replace("_", " ").title()
    horizon = f" · {row['horizon_days']} days" if row.get("horizon_days") else ""
    return f"{context} · {day(when)}{horizon} · {row['id'][:8]}"


def chart(fig, title, yaxis, xaxis="Date", height=350):
    fig.update_layout(title=dict(text=title, font=dict(size=16)), yaxis_title=yaxis, xaxis_title=xaxis,
                      template="plotly_white", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor=COLORS["surface"],
                      colorway=[COLORS["teal"], COLORS["blue"], COLORS["critical"]],
                      margin=dict(l=16, r=16, t=55, b=80), height=height,
                      legend=dict(orientation="h", y=-0.22, x=0), hovermode="x unified",
                      transition=dict(duration=0), separators=".,",
                      hoverlabel=dict(bgcolor=COLORS["surface"], font_color=COLORS["text"]),
                      font=dict(color=COLORS["text"], family="Segoe UI, sans-serif", size=12))
    fig.update_xaxes(showgrid=False, automargin=True)
    if xaxis == "Date":
        fig.update_xaxes(type="date", tickformat="%d %b %y", hoverformat="%d %b %Y")
    fig.update_yaxes(gridcolor=COLORS["border"], zerolinecolor=COLORS["border"], automargin=True)
    st.plotly_chart(fig, use_container_width=True, config={"displaylogo": False})


def line(fig, x, y, name, color=COLORS["teal"], dash="solid", **kwargs):
    fig.add_trace(go.Scatter(x=x, y=[number(value) for value in y], name=name, mode="lines", line=dict(color=color, dash=dash, width=2),
        hovertemplate="%{x|%d %b %Y}<br>%{y:,.1f} units<extra>%{fullData.name}</extra>", **kwargs))


def require_run(context):
    if not context.get("recommendation"):
        st.info('Select a saved analysis in the sidebar, or follow Data & Settings to prepare one.')
        return False
    return True
