"""Parlays page — parlay candidates and results."""
from __future__ import annotations

import streamlit as st
from app.dashboard.data_sources import (
    get_connection, get_parlay_summary, get_parlays_df, get_parlay_results_df,
)
from app.dashboard.components import section_header, empty_state, kpi_row
from app.dashboard.formatters import fmt_int, fmt_float, fmt_pct, fmt_roi


@st.cache_data(ttl=60)
def _load():
    conn = get_connection()
    summary = get_parlay_summary(conn)
    parlays = get_parlays_df(conn)
    results = get_parlay_results_df(conn)
    conn.close()
    return summary, parlays, results


def render() -> None:
    section_header("Parlays", "Candidatos a parlay y resultados historicos")

    try:
        summary, parlays, results = _load()
    except FileNotFoundError as exc:
        st.error(str(exc))
        return

    if summary:
        kpi_row([
            {"label": "Parlays Candidatos", "value": fmt_int(summary.get("total_candidates"))},
            {"label": "Parlays Cerrados", "value": fmt_int(summary.get("total_settled"))},
            {"label": "Win Rate", "value": fmt_pct(summary.get("win_rate"))},
            {"label": "ROI", "value": fmt_roi(summary.get("roi"))},
        ])
    else:
        empty_state("Sin datos de parlay disponibles")

    st.markdown("---")
    tab1, tab2 = st.tabs(["Candidatos", "Resultados"])

    with tab1:
        if parlays.empty:
            empty_state("Sin candidatos a parlay")
        else:
            st.dataframe(parlays, use_container_width=True, height=350)

    with tab2:
        if results.empty:
            empty_state("Sin resultados de parlays")
        else:
            st.dataframe(results, use_container_width=True, height=350)
