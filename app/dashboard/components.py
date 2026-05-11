"""Reusable Streamlit components for the dashboard."""
from __future__ import annotations

import streamlit as st
import pandas as pd
from typing import Any


def section_header(title: str, subtitle: str = "") -> None:
    st.markdown(f"### {title}")
    if subtitle:
        st.caption(subtitle)
    st.markdown("---")


def empty_state(message: str = "Sin datos disponibles", icon: str = "ℹ️") -> None:
    st.info(f"{icon} {message}")


def kpi_row(metrics: list[dict[str, Any]]) -> None:
    """Render a row of KPI metric cards.

    Each dict: {label, value, delta=None, help=None}
    """
    cols = st.columns(len(metrics))
    for col, m in zip(cols, metrics):
        with col:
            kwargs: dict[str, Any] = {
                "label": m.get("label", ""),
                "value": str(m.get("value", "N/A")),
            }
            if m.get("delta") is not None:
                kwargs["delta"] = m["delta"]
            if m.get("help"):
                kwargs["help"] = m["help"]
            st.metric(**kwargs)


def traffic_light_row(items: list[dict[str, Any]]) -> None:
    """Render a horizontal row of traffic-light indicators.

    Each dict: {label, light}  where light is "🟢"/"🟡"/"🔴"/"⚪"
    """
    cols = st.columns(len(items))
    for col, item in zip(cols, items):
        with col:
            st.markdown(f"**{item.get('light', '⚪')} {item.get('label', '')}**")


def data_table(df: pd.DataFrame, height: int = 300, use_container_width: bool = True) -> None:
    if df.empty:
        empty_state()
        return
    st.dataframe(df, height=height, use_container_width=use_container_width)


def gate_bar(passed: int, total: int) -> str:
    if total == 0:
        return "N/A"
    filled = round(passed / total * 10)
    bar = "█" * filled + "░" * (10 - filled)
    return f"{bar}  {passed}/{total}"


def status_badge(status: str | None) -> str:
    mapping = {
        "active": "🟢",
        "enabled": "🟢",
        "paused": "🟡",
        "shadow": "🟡",
        "disabled": "🔴",
        "error": "🔴",
    }
    key = (status or "").lower()
    icon = mapping.get(key, "⚪")
    return f"{icon} {status or 'N/A'}"


def collapsible_table(label: str, df: pd.DataFrame, height: int = 250) -> None:
    with st.expander(label, expanded=False):
        data_table(df, height=height)


def labeled_value(label: str, value: Any, help_text: str = "") -> None:
    if help_text:
        st.markdown(f"**{label}** — {value}", help=help_text)
    else:
        st.markdown(f"**{label}** — {value}")


def warning_banner(message: str) -> None:
    st.warning(f"⚠️ {message}")


def error_banner(message: str) -> None:
    st.error(f"🚨 {message}")


def success_banner(message: str) -> None:
    st.success(f"✅ {message}")
