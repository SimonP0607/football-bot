"""Overview page — system state at a glance."""
from __future__ import annotations

import streamlit as st
from app.dashboard.data_sources import get_connection, get_overview, get_all_table_counts
from app.dashboard.components import section_header, kpi_row, empty_state, traffic_light_row
from app.dashboard.formatters import fmt_int, fmt_pct, fmt_roi, badge_status


@st.cache_data(ttl=60)
def _load():
    conn = get_connection()
    data = get_overview(conn)
    table_counts = get_all_table_counts(conn)
    conn.close()
    return data, table_counts


def render() -> None:
    section_header("Overview", "Estado general del sistema")

    try:
        data, table_counts = _load()
    except FileNotFoundError as exc:
        st.error(str(exc))
        return

    # KPI row
    kpi_row([
        {"label": "Picks Hoy", "value": fmt_int(data.get("picks_today"))},
        {"label": "Candidatos", "value": fmt_int(data.get("picks_candidates"))},
        {"label": "Con Stake", "value": fmt_int(data.get("picks_with_stake"))},
        {"label": "Tablas DuckDB", "value": fmt_int(data.get("total_tables"))},
    ])

    st.markdown("---")
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Rendimiento (30d)")
        perf = data.get("performance", {})
        if perf:
            kpi_row([
                {"label": "ROI", "value": fmt_roi(perf.get("roi"))},
                {"label": "Hit Rate", "value": fmt_pct(perf.get("hit_rate"))},
                {"label": "Picks", "value": fmt_int(perf.get("total_picks"))},
            ])
        else:
            empty_state("Sin datos de performance")

    with col2:
        st.subheader("Bankroll")
        bk = data.get("bankroll", {})
        if bk:
            kpi_row([
                {"label": "Balance Actual", "value": fmt_int(bk.get("current_balance"))},
                {"label": "Unidades Base", "value": bk.get("base_unit", "N/A")},
                {"label": "Perfil", "value": bk.get("risk_profile", "N/A")},
            ])
        else:
            empty_state("Sin datos de bankroll")

    st.markdown("---")
    st.subheader("Estado de Tablas")

    if table_counts:
        cols = st.columns(4)
        for i, (table, count) in enumerate(sorted(table_counts.items())):
            with cols[i % 4]:
                icon = "🟢" if count and count > 0 else "⚪"
                st.markdown(f"{icon} **{table}** — {fmt_int(count) if count is not None else 'N/A'}")
    else:
        empty_state("No se pudo obtener conteo de tablas")
