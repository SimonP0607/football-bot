"""Governance page — experiments, gates, activation readiness."""
from __future__ import annotations

import streamlit as st
from app.dashboard.data_sources import (
    get_connection, get_governance_summary, get_experiment_registry,
)
from app.dashboard.components import section_header, empty_state, kpi_row, gate_bar
from app.dashboard.formatters import fmt_int, fmt_roi, badge_recommendation


@st.cache_data(ttl=120)
def _load():
    conn = get_connection()
    summary = get_governance_summary(conn)
    experiments = get_experiment_registry(conn)
    conn.close()
    return summary, experiments


def render() -> None:
    section_header("Governance", "Experimentos activos, gates de activacion y recomendaciones")

    try:
        summary, experiments = _load()
    except FileNotFoundError as exc:
        st.error(str(exc))
        return

    kpi_row([
        {"label": "Experimentos Activos", "value": fmt_int(summary.get("active_experiments"))},
        {"label": "Audits (7d)", "value": fmt_int(summary.get("decision_audits_7d"))},
        {"label": "Recomendaciones", "value": fmt_int(summary.get("total_recommendations"))},
        {"label": "Modulos Listos", "value": fmt_int(summary.get("modules_safe_to_use"))},
    ])

    st.markdown("---")
    st.subheader("Recomendaciones de Activacion")

    recs = summary.get("recommendations", [])
    if not recs:
        empty_state("Sin recomendaciones disponibles — ejecuta run_governance_job primero")
    else:
        for rec in recs:
            module = rec.get("module", "N/A")
            recommendation = rec.get("recommendation", "N/A")
            passed = rec.get("gates_passed", 0) or 0
            total = rec.get("gates_total", 0) or 0
            roi = rec.get("roi_30d")
            badge = badge_recommendation(recommendation)
            bar = gate_bar(passed, total)

            col1, col2, col3 = st.columns([2, 3, 2])
            with col1:
                st.markdown(f"**{module}**")
            with col2:
                st.markdown(f"{badge}  |  Gates: `{bar}`")
            with col3:
                st.markdown(f"ROI 30d: {fmt_roi(roi)}")

    st.markdown("---")
    st.subheader("Registro de Experimentos")

    if experiments.empty:
        empty_state("Sin experimentos registrados")
    else:
        st.dataframe(experiments, use_container_width=True, height=300)

    st.markdown("---")
    st.caption(
        "MODEL_GOVERNANCE_AUTO_ACTIVATE siempre es false. "
        "Las recomendaciones son informativas — la activacion es manual via variables de entorno."
    )
