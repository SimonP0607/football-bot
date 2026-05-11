"""Performance page — ROI, hit rate, cumulative profit curve."""
from __future__ import annotations

import streamlit as st
import pandas as pd
from app.dashboard.data_sources import (
    get_connection, get_performance_summary, get_performance_by_market,
    get_performance_by_league, get_cumulative_profit,
)
from app.dashboard.components import section_header, empty_state, kpi_row
from app.dashboard.formatters import fmt_roi, fmt_pct, fmt_int, fmt_float


@st.cache_data(ttl=120)
def _load(days: int):
    conn = get_connection()
    summary = get_performance_summary(conn, days)
    by_market = get_performance_by_market(conn, days)
    by_league = get_performance_by_league(conn, days)
    cumulative = get_cumulative_profit(conn, days)
    conn.close()
    return summary, by_market, by_league, cumulative


def render() -> None:
    section_header("Performance", "Rendimiento historico por mercado y liga")

    days = st.sidebar.selectbox("Ventana (dias)", [7, 14, 30, 60, 90], index=2)

    try:
        summary, by_market, by_league, cumulative = _load(days)
    except FileNotFoundError as exc:
        st.error(str(exc))
        return

    if summary:
        kpi_row([
            {"label": "ROI", "value": fmt_roi(summary.get("roi"))},
            {"label": "Hit Rate", "value": fmt_pct(summary.get("hit_rate"))},
            {"label": "Total Picks", "value": fmt_int(summary.get("total_picks"))},
            {"label": "Profit Total", "value": fmt_float(summary.get("total_profit"), decimals=2)},
        ])
    else:
        st.info("Sin datos de performance para la ventana seleccionada")

    st.markdown("---")

    if not cumulative.empty and "cumulative_profit" in cumulative.columns:
        try:
            import plotly.graph_objects as go
            fig = go.Figure()
            x_col = next((c for c in ["settled_at", "created_at", "date"] if c in cumulative.columns), None)
            if x_col:
                fig.add_trace(go.Scatter(
                    x=cumulative[x_col],
                    y=cumulative["cumulative_profit"],
                    mode="lines",
                    name="Profit Acumulado",
                    line=dict(color="#00cc66", width=2),
                    fill="tozeroy",
                    fillcolor="rgba(0,204,102,0.15)",
                ))
            else:
                fig.add_trace(go.Scatter(
                    y=cumulative["cumulative_profit"],
                    mode="lines",
                    name="Profit Acumulado",
                    line=dict(color="#00cc66", width=2),
                ))
            fig.update_layout(
                title=f"Profit Acumulado ({days}d)",
                xaxis_title="Fecha",
                yaxis_title="Profit (unidades)",
                height=350,
                margin=dict(l=40, r=20, t=40, b=40),
            )
            st.plotly_chart(fig, use_container_width=True)
        except ImportError:
            st.line_chart(cumulative.set_index(x_col)["cumulative_profit"] if x_col else cumulative["cumulative_profit"])
    else:
        empty_state("Sin datos para la curva de profit")

    st.markdown("---")
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Por Mercado")
        if by_market.empty:
            empty_state("Sin datos por mercado")
        else:
            st.dataframe(by_market, use_container_width=True)

    with col2:
        st.subheader("Por Liga")
        if by_league.empty:
            empty_state("Sin datos por liga")
        else:
            st.dataframe(by_league, use_container_width=True)
