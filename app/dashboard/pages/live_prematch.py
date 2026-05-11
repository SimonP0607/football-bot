"""Live & Prematch page — live snapshots, market movement signals, CLV."""
from __future__ import annotations

import streamlit as st
from app.dashboard.data_sources import (
    get_connection, get_live_snapshots, get_prematch_intelligence,
    get_market_movement_signals, get_pick_clv_recent,
)
from app.dashboard.components import section_header, empty_state, kpi_row
from app.dashboard.formatters import fmt_int, fmt_float


@st.cache_data(ttl=30)
def _load():
    conn = get_connection()
    live = get_live_snapshots(conn)
    prematch = get_prematch_intelligence(conn)
    signals = get_market_movement_signals(conn)
    clv = get_pick_clv_recent(conn)
    conn.close()
    return live, prematch, signals, clv


def render() -> None:
    section_header("Live & Prematch", "Snapshots en vivo, movimientos de mercado y CLV")

    try:
        live, prematch, signals, clv = _load()
    except FileNotFoundError as exc:
        st.error(str(exc))
        return

    kpi_row([
        {"label": "Snapshots Live", "value": fmt_int(len(live))},
        {"label": "Prematch Intel", "value": fmt_int(len(prematch))},
        {"label": "Market Signals", "value": fmt_int(len(signals))},
        {"label": "CLV Recientes", "value": fmt_int(len(clv))},
    ])

    st.markdown("---")
    tab1, tab2, tab3, tab4 = st.tabs(["Live Snapshots", "Prematch Intel", "Market Signals", "CLV"])

    with tab1:
        if live.empty:
            empty_state("Sin snapshots live — live_monitoring puede no estar activo")
        else:
            st.dataframe(live, use_container_width=True, height=350)

    with tab2:
        if prematch.empty:
            empty_state("Sin datos de prematch intelligence")
        else:
            st.dataframe(prematch, use_container_width=True, height=350)

    with tab3:
        if signals.empty:
            empty_state("Sin senales de movimiento de mercado")
        else:
            st.dataframe(signals, use_container_width=True, height=350)

    with tab4:
        st.caption("CLV (Closing Line Value) — valor capturado vs linea de cierre")
        if clv.empty:
            empty_state("Sin datos CLV recientes")
        else:
            clv_cols = [c for c in ["created_at", "match_id", "market", "opening_odds", "closing_odds", "clv_pct", "bet_odds"] if c in clv.columns]
            df_show = clv[clv_cols].copy() if clv_cols else clv
            st.dataframe(df_show, use_container_width=True, height=350)
