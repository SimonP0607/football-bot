"""API Budget page — usage tracking and sync runs."""
from __future__ import annotations

import streamlit as st
from app.dashboard.data_sources import (
    get_connection, get_api_usage_summary, get_api_usage_df, get_sync_runs_df,
)
from app.dashboard.components import section_header, empty_state, kpi_row
from app.dashboard.formatters import fmt_int, fmt_float


@st.cache_data(ttl=60)
def _load():
    conn = get_connection()
    summary = get_api_usage_summary(conn)
    usage_df = get_api_usage_df(conn)
    sync_df = get_sync_runs_df(conn)
    conn.close()
    return summary, usage_df, sync_df


def render() -> None:
    section_header("API Budget", "Uso de API-Football y sincronizaciones")

    try:
        summary, usage_df, sync_df = _load()
    except FileNotFoundError as exc:
        st.error(str(exc))
        return

    if summary:
        kpi_row([
            {"label": "Llamadas Hoy", "value": fmt_int(summary.get("calls_today"))},
            {"label": "Llamadas Mes", "value": fmt_int(summary.get("calls_month"))},
            {"label": "Limite Diario", "value": fmt_int(summary.get("daily_limit"))},
            {"label": "% Usado Hoy", "value": f"{summary.get('pct_used_today', 0):.1f}%"},
        ])

        if summary.get("calls_today") and summary.get("daily_limit"):
            pct = summary.get("pct_used_today", 0)
            color = "normal" if pct < 70 else ("off" if pct < 90 else "inverse")
            st.progress(min(pct / 100, 1.0), text=f"Budget diario: {pct:.1f}%")
    else:
        empty_state("Sin datos de uso de API — revisa api_usage o api_budget_log")

    st.markdown("---")
    tab1, tab2 = st.tabs(["Uso por Endpoint", "Sincronizaciones"])

    with tab1:
        if usage_df.empty:
            empty_state("Sin registros de uso de API")
        else:
            st.dataframe(usage_df, use_container_width=True, height=300)

            if "endpoint" in usage_df.columns and "call_count" in usage_df.columns:
                try:
                    import plotly.express as px
                    top = usage_df.groupby("endpoint")["call_count"].sum().reset_index().sort_values("call_count", ascending=False).head(10)
                    fig = px.bar(top, x="endpoint", y="call_count", title="Top Endpoints por Llamadas")
                    fig.update_layout(height=300, margin=dict(l=40, r=20, t=40, b=80))
                    st.plotly_chart(fig, use_container_width=True)
                except ImportError:
                    pass

    with tab2:
        if sync_df.empty:
            empty_state("Sin sincronizaciones registradas")
        else:
            cols_show = [c for c in ["started_at", "ended_at", "status", "records_fetched", "error_message"] if c in sync_df.columns]
            df_show = sync_df[cols_show].copy() if cols_show else sync_df
            st.dataframe(df_show, use_container_width=True, height=350)
