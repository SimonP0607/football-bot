"""Phase 16: Local Admin Dashboard entry point."""
from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="Football Bot Dashboard",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

PAGES = {
    "Overview": "overview",
    "Picks del Dia": "picks",
    "Performance": "performance",
    "Governance": "governance",
    "Bankroll": "bankroll",
    "Parlays": "parlays",
    "API Budget": "api_budget",
    "Live & Prematch": "live_prematch",
    "Entities & Players": "entities_players",
    "System Health": "system_health",
}

st.sidebar.title("⚽ Football Bot")
st.sidebar.caption("Local Admin Dashboard — read-only")
st.sidebar.markdown("---")

selection = st.sidebar.radio("Navegacion", list(PAGES.keys()), index=0)

st.sidebar.markdown("---")
st.sidebar.caption("Fase 16 — v1.0  |  read-only")

page_key = PAGES[selection]

if page_key == "overview":
    from app.dashboard.pages.overview import render
elif page_key == "picks":
    from app.dashboard.pages.picks import render
elif page_key == "performance":
    from app.dashboard.pages.performance import render
elif page_key == "governance":
    from app.dashboard.pages.governance import render
elif page_key == "bankroll":
    from app.dashboard.pages.bankroll import render
elif page_key == "parlays":
    from app.dashboard.pages.parlays import render
elif page_key == "api_budget":
    from app.dashboard.pages.api_budget import render
elif page_key == "live_prematch":
    from app.dashboard.pages.live_prematch import render
elif page_key == "entities_players":
    from app.dashboard.pages.entities_players import render
elif page_key == "system_health":
    from app.dashboard.pages.system_health import render
else:
    def render():
        st.error("Pagina no encontrada")

try:
    render()
except FileNotFoundError as exc:
    st.error(f"Base de datos no encontrada: {exc}")
except Exception as exc:
    st.error(f"Error inesperado: {exc}")
    with st.expander("Detalle del error"):
        import traceback
        st.code(traceback.format_exc())
