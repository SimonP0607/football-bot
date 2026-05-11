"""Entities & Players page — player search, signals, hot players."""
from __future__ import annotations

import streamlit as st
from app.dashboard.data_sources import (
    get_connection, search_players, get_player_signals, get_hot_players, get_team_list,
)
from app.dashboard.components import section_header, empty_state, kpi_row
from app.dashboard.formatters import fmt_int


@st.cache_data(ttl=120)
def _load_hot():
    conn = get_connection()
    hot = get_hot_players(conn)
    teams = get_team_list(conn)
    conn.close()
    return hot, teams


def render() -> None:
    section_header("Entities & Players", "Jugadores en forma, senales y busqueda de entidades")

    try:
        hot, teams = _load_hot()
    except FileNotFoundError as exc:
        st.error(str(exc))
        return

    tab1, tab2, tab3 = st.tabs(["Hot Players", "Buscar Jugador", "Equipos"])

    with tab1:
        st.caption("Jugadores con mayor actividad de senales recientes")
        if hot.empty:
            empty_state("Sin hot players — player_signals puede estar vacia")
        else:
            kpi_row([{"label": "Hot Players", "value": fmt_int(len(hot))}])
            st.dataframe(hot, use_container_width=True, height=350)

    with tab2:
        query = st.text_input("Buscar jugador (nombre o ID)", placeholder="ej: Mbappe")
        if query:
            with st.spinner("Buscando..."):
                conn = get_connection()
                results = search_players(conn, query)
                if not results.empty:
                    selected_id = st.selectbox(
                        "Seleccionar jugador",
                        options=results.get("player_id", results.index).tolist() if "player_id" in results.columns else results.index.tolist(),
                        format_func=lambda x: str(x),
                    )
                    signals = get_player_signals(conn, selected_id)
                    conn.close()

                    st.subheader("Datos del Jugador")
                    st.dataframe(results, use_container_width=True)

                    st.subheader("Senales Recientes")
                    if signals.empty:
                        empty_state("Sin senales para este jugador")
                    else:
                        st.dataframe(signals, use_container_width=True, height=300)
                else:
                    conn.close()
                    empty_state(f"No se encontraron jugadores para: '{query}'")
        else:
            st.caption("Escribe un nombre o ID para buscar")

    with tab3:
        st.caption("Equipos registrados en la base local")
        if teams.empty:
            empty_state("Sin equipos registrados")
        else:
            kpi_row([{"label": "Equipos", "value": fmt_int(len(teams))}])
            filter_name = st.text_input("Filtrar por nombre", placeholder="ej: Barcelona")
            if filter_name and "name" in teams.columns:
                display = teams[teams["name"].str.contains(filter_name, case=False, na=False)]
            else:
                display = teams
            st.dataframe(display, use_container_width=True, height=350)
