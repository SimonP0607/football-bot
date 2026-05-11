"""Picks page — today's picks and candidates."""
from __future__ import annotations

import streamlit as st
import pandas as pd
from app.dashboard.data_sources import (
    get_connection, get_picks_today, get_picks_candidates, get_picks_with_stake,
)
from app.dashboard.components import section_header, empty_state, kpi_row, data_table
from app.dashboard.formatters import fmt_int, fmt_float, fmt_pct, badge_confidence


@st.cache_data(ttl=30)
def _load():
    conn = get_connection()
    today = get_picks_today(conn)
    candidates = get_picks_candidates(conn)
    with_stake = get_picks_with_stake(conn)
    conn.close()
    return today, candidates, with_stake


def render() -> None:
    section_header("Picks del Dia", "Picks oficiales, candidatos y stakes recomendados")

    try:
        today, candidates, with_stake = _load()
    except FileNotFoundError as exc:
        st.error(str(exc))
        return

    kpi_row([
        {"label": "Picks Hoy (shadow)", "value": fmt_int(len(today))},
        {"label": "Candidatos", "value": fmt_int(len(candidates))},
        {"label": "Con Stake", "value": fmt_int(len(with_stake))},
    ])

    st.markdown("---")

    tab1, tab2, tab3 = st.tabs(["Shadow Picks", "Candidatos", "Con Stake"])

    with tab1:
        st.caption("shadow_value_picks — picks del motor de valor (modo observacion)")
        if today.empty:
            empty_state("Sin picks shadow hoy")
        else:
            cols_show = [c for c in ["created_at", "match_id", "market", "pick", "value_score", "confidence", "odds", "status"] if c in today.columns]
            df_show = today[cols_show].copy() if cols_show else today
            if "confidence" in df_show.columns:
                df_show["confidence"] = df_show["confidence"].apply(badge_confidence)
            data_table(df_show, height=350)

    with tab2:
        st.caption("pick_candidates — todos los candidatos evaluados")
        if candidates.empty:
            empty_state("Sin candidatos disponibles")
        else:
            st.dataframe(candidates, height=350, use_container_width=True)

    with tab3:
        st.caption("stake_recommendations — candidatos con stake asignado")
        if with_stake.empty:
            empty_state("Sin stakes recomendados")
        else:
            st.dataframe(with_stake, height=350, use_container_width=True)
