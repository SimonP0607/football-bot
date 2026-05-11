"""Bankroll page — profile, portfolio snapshot, stake recommendations."""
from __future__ import annotations

import streamlit as st
from app.dashboard.data_sources import (
    get_connection, get_bankroll_summary, get_stake_recommendations_df, get_portfolio_history,
)
from app.dashboard.components import section_header, empty_state, kpi_row
from app.dashboard.formatters import fmt_int, fmt_float, fmt_pct


@st.cache_data(ttl=60)
def _load():
    conn = get_connection()
    summary = get_bankroll_summary(conn)
    stakes = get_stake_recommendations_df(conn)
    history = get_portfolio_history(conn)
    conn.close()
    return summary, stakes, history


def render() -> None:
    section_header("Bankroll", "Perfil de riesgo, portafolio y stakes recomendados")

    try:
        summary, stakes, history = _load()
    except FileNotFoundError as exc:
        st.error(str(exc))
        return

    if summary:
        kpi_row([
            {"label": "Balance Actual", "value": fmt_float(summary.get("current_balance"), decimals=2)},
            {"label": "Balance Inicial", "value": fmt_float(summary.get("initial_balance"), decimals=2)},
            {"label": "Unidad Base", "value": fmt_float(summary.get("base_unit"), decimals=2)},
            {"label": "Perfil Riesgo", "value": summary.get("risk_profile", "N/A")},
        ])

        st.markdown("---")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Max Stake %", fmt_pct(summary.get("max_stake_pct")))
        with col2:
            st.metric("Kelly Fraction", fmt_float(summary.get("kelly_fraction")))
        with col3:
            st.metric("Drawdown Max", fmt_pct(summary.get("max_drawdown_pct")))
    else:
        empty_state("Sin perfil de bankroll configurado — revisa bankroll_profiles")

    st.markdown("---")
    st.subheader("Stakes Recomendados")

    if stakes.empty:
        empty_state("Sin stake recommendations recientes")
    else:
        cols_show = [c for c in ["created_at", "match_id", "market", "pick", "recommended_stake", "kelly_stake", "confidence"] if c in stakes.columns]
        df_show = stakes[cols_show].copy() if cols_show else stakes
        st.dataframe(df_show, use_container_width=True, height=300)

    st.markdown("---")
    st.subheader("Historial de Portafolio")

    if history.empty:
        empty_state("Sin historial de portafolio")
    else:
        try:
            import plotly.graph_objects as go
            x_col = next((c for c in ["recorded_at", "created_at", "date"] if c in history.columns), None)
            y_col = next((c for c in ["balance", "current_balance", "equity"] if c in history.columns), None)
            if x_col and y_col:
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=history[x_col], y=history[y_col],
                    mode="lines+markers",
                    name="Balance",
                    line=dict(color="#4e8ef7", width=2),
                ))
                fig.update_layout(title="Evolucion del Balance", height=300, margin=dict(l=40, r=20, t=40, b=40))
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.dataframe(history, use_container_width=True)
        except ImportError:
            st.dataframe(history, use_container_width=True)
