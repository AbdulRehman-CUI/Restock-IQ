from datetime import date
from decimal import Decimal
from html import escape
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from app.ui.api_client import APIClient, APIError

NO_ACTION = "Projected shortages assume no additional recommended orders are placed."
COLORS = dict(background="#F5F7FA", surface="#FFFFFF", text="#152238", muted="#64748B",
              border="#E2E8F0", teal="#0F766E", hover="#115E59", subtle="#ECFDF5",
              warning="#B45309", warning_bg="#FFFBEB", critical="#B91C1C",
              critical_bg="#FEF2F2", blue="#2563EB")


def theme():
    # Stable Streamlit component selectors only; sidebar keeps native light controls.
    st.markdown(f"""<style>
    .stApp {{background:{COLORS['background']};color:{COLORS['text']};
      font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}}
    .block-container {{padding-top:2rem;padding-bottom:3rem;max-width:1560px}}
    h1 {{font-size:2rem!important;letter-spacing:-.04em}}
    h2 {{font-size:1.3rem!important;letter-spacing:-.02em}}
    h3 {{font-size:1.05rem!important}}
    [data-testid="stSidebar"] {{background:{COLORS['surface']};border-right:1px solid {COLORS['border']}}}
    [data-testid="stMetric"] {{background:{COLORS['surface']};border:1px solid {COLORS['border']};
      border-radius:10px;padding:16px;min-height:110px}}
    [data-testid="stMetricValue"] {{font-size:1.6rem;font-variant-numeric:tabular-nums;letter-spacing:-.03em}}
    [data-testid="stCaptionContainer"] {{color:{COLORS['muted']}}}
    .rq-brand {{font-size:1.5rem;font-weight:700;letter-spacing:-.05em;margin-bottom:4px}}
    .rq-brand span {{color:{COLORS['teal']}}}
    .rq-badge {{display:inline-block;border-radius:5px;padding:4px 9px;font-size:.8rem;
      background:{COLORS['subtle']};color:{COLORS['hover']};margin:0 6px 6px 0}}
    .rq-badge.warning {{background:{COLORS['warning_bg']};color:{COLORS['warning']}}}
    .rq-badge.critical {{background:{COLORS['critical_bg']};color:{COLORS['critical']}}}
    .rq-context {{font-size:.85rem;color:{COLORS['muted']};margin-bottom:1.2rem;line-height:1.8}}
    button:focus-visible {{outline:2px solid {COLORS['teal']};outline-offset:3px}}
    </style>""", unsafe_allow_html=True)


def badge(text, tone=""):
    st.markdown(f'<span class="rq-badge {escape(tone, quote=True)}">{escape(str(text))}</span>', unsafe_allow_html=True)


def header(title, description):
    st.title(title)
    st.caption(description)


def metric(column, label, value, caption):
    column.metric(label, value)
    column.caption(caption)


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
        table(exc.payload["errors"])
    if exc.payload:
        with st.expander("Detailed error report"):
            st.json(exc.payload)
    st.caption("Completed runs remain saved. If a request timed out, refresh discovery before submitting again.")


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
        st.info("Create or select a recommendation run in Data & Settings to view decisions.")
        return False
    return True
