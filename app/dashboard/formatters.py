"""Display helpers for the dashboard."""
from __future__ import annotations

import math


def fmt_pct(value, decimals: int = 1, default: str = "N/A") -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return default
    return f"{float(value) * 100:.{decimals}f}%"


def fmt_roi(value, decimals: int = 2, default: str = "N/A") -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return default
    return f"{float(value) * 100:+.{decimals}f}%"


def fmt_float(value, decimals: int = 2, default: str = "N/A") -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return default
    return f"{float(value):.{decimals}f}"


def fmt_int(value, default: str = "N/A") -> str:
    if value is None:
        return default
    try:
        return f"{int(value):,}"
    except (ValueError, TypeError):
        return default


def fmt_currency(value, symbol: str = "u", decimals: int = 2, default: str = "N/A") -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return default
    return f"{symbol}{float(value):.{decimals}f}"


def fmt_odds(value, decimals: int = 2, default: str = "N/A") -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return default
    return f"{float(value):.{decimals}f}"


def badge_confidence(confidence: str | None) -> str:
    mapping = {
        "HIGH": "🟢 HIGH",
        "MEDIUM": "🟡 MEDIUM",
        "LOW": "🔴 LOW",
    }
    return mapping.get(str(confidence).upper() if confidence else "", "⚪ N/A")


def badge_recommendation(rec: str | None) -> str:
    mapping = {
        "SAFE_TO_USE_FOR_SELECTION": "🟢 SAFE TO USE",
        "SAFE_TO_TEST_ASSIST": "🟡 TEST (ASSIST)",
        "SAFE_TO_TEST_SHADOW": "🟡 TEST (SHADOW)",
        "OBSERVE_MORE": "🟠 OBSERVE MORE",
        "DO_NOT_ACTIVATE": "🔴 DO NOT ACTIVATE",
    }
    return mapping.get(str(rec).upper() if rec else "", f"⚪ {rec or 'N/A'}")


def badge_status(status: str | None) -> str:
    mapping = {
        "active": "🟢 active",
        "enabled": "🟢 enabled",
        "running": "🟢 running",
        "ok": "🟢 ok",
        "paused": "🟡 paused",
        "shadow": "🟡 shadow",
        "disabled": "🔴 disabled",
        "stopped": "🔴 stopped",
        "error": "🔴 error",
    }
    s = str(status).lower() if status else ""
    return mapping.get(s, f"⚪ {status or 'N/A'}")


def traffic_light(value: float | None, green_thresh: float, red_thresh: float, higher_is_better: bool = True) -> str:
    """Return a color emoji based on thresholds."""
    if value is None:
        return "⚪"
    if higher_is_better:
        if value >= green_thresh:
            return "🟢"
        if value <= red_thresh:
            return "🔴"
        return "🟡"
    else:
        if value <= green_thresh:
            return "🟢"
        if value >= red_thresh:
            return "🔴"
        return "🟡"


def color_delta(value: float | None, default: str = "—") -> str:
    """Return colored arrow string for a delta."""
    if value is None:
        return default
    if value > 0:
        return f"▲ {value:+.2f}"
    if value < 0:
        return f"▼ {value:.2f}"
    return f"= {value:.2f}"


def truncate(text: str, max_len: int = 40) -> str:
    if not text:
        return ""
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def mask_secret(value: str | None, visible: int = 4) -> str:
    if not value:
        return "—"
    if len(value) <= visible:
        return "****"
    return value[:visible] + "****"
