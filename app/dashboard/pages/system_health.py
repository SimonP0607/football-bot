"""System Health page — table counts, scheduler runs, config summary."""
from __future__ import annotations

import streamlit as st
from app.dashboard.data_sources import (
    get_connection, get_all_table_counts, get_scheduler_runs, get_config_summary,
    list_all_tables,
)
from app.dashboard.components import section_header, empty_state, kpi_row
from app.dashboard.formatters import fmt_int, badge_status


@st.cache_data(ttl=60)
def _load():
    conn = get_connection()
    table_counts = get_all_table_counts(conn)
    all_tables = list_all_tables(conn)
    scheduler = get_scheduler_runs(conn)
    config = get_config_summary()
    conn.close()
    return table_counts, all_tables, scheduler, config


def render() -> None:
    section_header("System Health", "Estado de tablas, scheduler y configuracion del sistema")

    try:
        table_counts, all_tables, scheduler, config = _load()
    except FileNotFoundError as exc:
        st.error(str(exc))
        return

    total_tables = len(all_tables)
    populated = sum(1 for v in table_counts.values() if v and v > 0)

    kpi_row([
        {"label": "Total Tablas", "value": fmt_int(total_tables)},
        {"label": "Con Datos", "value": fmt_int(populated)},
        {"label": "Scheduler Runs (7d)", "value": fmt_int(len(scheduler))},
    ])

    st.markdown("---")
    tab1, tab2, tab3 = st.tabs(["Tablas DuckDB", "Scheduler", "Configuracion"])

    with tab1:
        if not table_counts:
            empty_state("Sin informacion de tablas")
        else:
            cols = st.columns(3)
            for i, (table, count) in enumerate(sorted(table_counts.items())):
                with cols[i % 3]:
                    if count and count > 0:
                        icon = "🟢"
                    else:
                        icon = "⚪"
                    st.markdown(f"{icon} **{table}**  \n{fmt_int(count) if count is not None else 'N/A'} filas")

    with tab2:
        if scheduler.empty:
            empty_state("Sin runs del scheduler registrados")
        else:
            cols_show = [c for c in ["started_at", "job_name", "status", "duration_ms", "error_message"] if c in scheduler.columns]
            df_show = scheduler[cols_show].copy() if cols_show else scheduler

            if "status" in df_show.columns:
                df_show["status"] = df_show["status"].apply(badge_status)

            st.dataframe(df_show, use_container_width=True, height=400)

    with tab3:
        if not config:
            empty_state("No se pudo cargar la configuracion")
        else:
            st.caption("Los secretos estan enmascarados")
            col1, col2 = st.columns(2)
            items = list(config.items())
            mid = len(items) // 2
            for col, chunk in [(col1, items[:mid]), (col2, items[mid:])]:
                with col:
                    for key, value in chunk:
                        st.markdown(f"**{key}**: `{value}`")
