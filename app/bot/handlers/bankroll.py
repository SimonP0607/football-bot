"""Phase 14: Telegram handlers for /bankroll, /riesgo, /stake.

Commands:
  /bankroll          — active profile + portfolio snapshot
  /riesgo            — portfolio risk level, warnings, exposure
  /stake <units>     — explain stake sizing methodology
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.utils import send_html
from app.core.config import settings

logger = logging.getLogger(__name__)

_DISCLAIMER = (
    "\n<i>El bankroll engine es una herramienta de analisis. "
    "No constituye consejo financiero.</i>"
)

_RISK_ICON = {
    "conservative": "++",
    "balanced":     "=",
    "aggressive":   "--",
    "unsafe":       "!!",
}


def _icon(level: str | None) -> str:
    return _RISK_ICON.get(level or "", "?")


def _u(v: float | None, decimals: int = 2) -> str:
    if v is None:
        return "n/a"
    return f"{v:.{decimals}f}u"


def _pct(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.1f}%"


def _build_profile_block(profile: dict) -> str:
    lines = [
        "<b>Perfil activo</b>",
        f"  Nombre:          <b>{profile.get('profile_name', 'default')}</b>",
        f"  Bankroll:        <b>{profile.get('bankroll_units', 100):.0f}u</b>",
        f"  Kelly fraction:  <b>{profile.get('kelly_fraction', 0.25):.2f}</b>",
        f"  Max/pick:        <b>{_u(profile.get('max_pick_risk_units'))}</b>",
        f"  Max/dia:         <b>{_u(profile.get('max_daily_risk_units'))}</b>",
        f"  Min edge:        <b>{profile.get('min_edge_for_stake', 0.02)*100:.1f}%</b>",
        f"  Min confianza:   <b>{profile.get('min_confidence_for_stake', 0.52)*100:.0f}%</b>",
    ]
    return "\n".join(lines)


def _build_snapshot_block(snap: dict) -> str:
    score = snap.get("portfolio_score")
    level = snap.get("risk_level") or "unknown"
    icon  = _icon(level)
    score_str = f"{score:.1f}/100" if score is not None else "n/a"
    lines = [
        "",
        "<b>Portfolio hoy</b>",
        f"  Score:    <b>[{icon}] {score_str}</b> — {level.upper()}",
        f"  Picks:    <b>{snap.get('total_picks', 0)}</b>",
        f"  Unidades: <b>{_u(snap.get('total_recommended_units'))}</b>",
    ]
    warnings = snap.get("warnings_json") or []
    if isinstance(warnings, list) and warnings:
        lines.append("  Alertas:")
        for w in warnings[:5]:
            lines.append(f"    - {w}")
    return "\n".join(lines)


@require_auth
async def bankroll_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show active bankroll profile + today's portfolio snapshot."""
    if not settings.bankroll_engine_enabled:
        await send_html(
            update,
            "<b>Bankroll Engine</b>\n\n"
            "Desactivado. Activa con <code>BANKROLL_ENGINE_ENABLED=true</code>.",
        )
        return

    try:
        from app.data.local.duckdb_client import get_local_db, init_schema
        from app.data.local.bankroll_risk_repo import (
            get_active_bankroll_profile,
            get_latest_portfolio_snapshot,
            get_bankroll_summary,
        )

        conn = get_local_db()
        init_schema(conn)

        profile = get_active_bankroll_profile(conn)
        snap    = get_latest_portfolio_snapshot(conn)
        summary = get_bankroll_summary(conn, days=30)

        lines = ["<b>Bankroll Engine</b>", ""]

        if profile:
            lines.append(_build_profile_block(profile))
        else:
            lines.append("<i>Sin perfil activo — usando defaults de configuracion.</i>")

        if snap:
            lines.append(_build_snapshot_block(snap))
        else:
            lines.append("\n<i>Sin snapshot de portfolio. Ejecuta /riesgo para calcular.</i>")

        lines.append("")
        total_recs = summary.get("total_recommendations", 0)
        with_stake = summary.get("with_stake", 0)
        rejected   = summary.get("rejected", 0)
        if total_recs > 0:
            lines.append("<b>Ultimos 30 dias</b>")
            lines.append(f"  Recomendaciones: <b>{total_recs}</b>")
            lines.append(f"  Con stake: <b>{with_stake}</b>  Rechazados: <b>{rejected}</b>")
            avg_u = summary.get("avg_recommended_units")
            if avg_u:
                lines.append(f"  Avg unidades: <b>{avg_u:.2f}u</b>")

        lines.append(_DISCLAIMER)
        await send_html(update, "\n".join(lines))

    except Exception as exc:
        logger.error("bankroll_handler: %s", exc)
        await send_html(update, "<b>Bankroll Engine</b>\n\nError al obtener datos.")


@require_auth
async def riesgo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show portfolio risk level, warnings, and exposure breakdown."""
    if not settings.bankroll_engine_enabled:
        await send_html(
            update,
            "<b>Riesgo del Portfolio</b>\n\n"
            "Bankroll engine desactivado.",
        )
        return

    try:
        from app.data.local.duckdb_client import get_local_db, init_schema
        from app.data.local.bankroll_risk_repo import (
            get_latest_portfolio_snapshot,
            get_risk_events,
        )

        conn = get_local_db()
        init_schema(conn)

        snap   = get_latest_portfolio_snapshot(conn)
        events = get_risk_events(conn, days=7)

        lines = ["<b>Riesgo del Portfolio</b>", ""]

        if not snap:
            lines.append("<i>Sin datos de portfolio. Ejecuta el bankroll engine primero.</i>")
            await send_html(update, "\n".join(lines))
            return

        score = snap.get("portfolio_score")
        level = snap.get("risk_level") or "unknown"
        icon  = _icon(level)
        score_str = f"{score:.1f}/100" if score is not None else "n/a"
        lines.append(f"<b>Score: [{icon}] {score_str} — {level.upper()}</b>")
        lines.append(f"Fecha: {snap.get('snapshot_date', 'hoy')}")
        lines.append(f"Picks: {snap.get('total_picks', 0)}  |  "
                     f"Unidades: {_u(snap.get('total_recommended_units'))}")
        lines.append("")

        warnings = snap.get("warnings_json") or []
        if isinstance(warnings, list) and warnings:
            lines.append("<b>Alertas activas</b>")
            for w in warnings:
                lines.append(f"  - {w}")
            lines.append("")

        exp_lg = snap.get("exposure_by_league_json") or {}
        if isinstance(exp_lg, dict) and exp_lg:
            lines.append("<b>Exposicion por liga</b>")
            for lg_id, units in sorted(exp_lg.items(), key=lambda x: -x[1])[:5]:
                lines.append(f"  Liga {lg_id}: <b>{units:.2f}u</b>")
            lines.append("")

        exp_mkt = snap.get("exposure_by_market_json") or {}
        if isinstance(exp_mkt, dict) and exp_mkt:
            lines.append("<b>Exposicion por mercado</b>")
            for mkt, units in sorted(exp_mkt.items(), key=lambda x: -x[1])[:5]:
                lines.append(f"  {mkt}: <b>{units:.2f}u</b>")
            lines.append("")

        if events:
            recent = events[:3]
            lines.append("<b>Eventos recientes (7d)</b>")
            for ev in recent:
                sev = ev.get("severity", "?")
                etype = ev.get("event_type", "?")
                msg = ev.get("message", "")[:60]
                lines.append(f"  [{sev.upper()}] {etype}: {msg}")

        lines.append(_DISCLAIMER)
        await send_html(update, "\n".join(lines))

    except Exception as exc:
        logger.error("riesgo_handler: %s", exc)
        await send_html(update, "<b>Riesgo del Portfolio</b>\n\nError al obtener datos.")


@require_auth
async def stake_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Explain stake sizing methodology and show current settings."""
    lines = [
        "<b>Stake Sizing — Como funciona</b>",
        "",
        "El stake se calcula usando el <b>Criterio de Kelly Fraccional</b>:",
        "",
        "<code>f* = (p*b - q) / b</code>",
        "donde <code>p</code>=probabilidad modelo, <code>b</code>=odds-1, <code>q</code>=1-p",
        "",
        f"<b>Configuracion actual</b>",
        f"  Kelly fraction:  <b>{settings.bankroll_kelly_fraction:.2f}</b> (25% del Kelly completo)",
        f"  Max stake/pick:  <b>{settings.bankroll_max_pick_units:.1f}u</b>",
        f"  Max stake/dia:   <b>{settings.bankroll_max_daily_units:.1f}u</b>",
        f"  Min edge:        <b>{settings.bankroll_min_edge*100:.1f}%</b>",
        f"  Min confianza:   <b>{settings.bankroll_min_confidence*100:.0f}%</b>",
        "",
        "<b>Labels de stake</b>",
        "  no_stake → 0u   |   micro → &lt;0.5u",
        "  small → 0.5-1u  |   medium → 1-1.5u  |   large → &gt;1.5u",
        "",
        "<b>Ajustes automaticos (solo hacia abajo)</b>",
        "  - Estrategia 'reduce': x0.50",
        "  - Estrategia 'monitor': x0.85",
        "  - Muestra insuficiente (&lt;10): x0.75",
        "  - CLV negativo (&lt;-3%): x0.60",
        "",
        "Motor: <b>{}ACTIVADO</b>".format(
            "" if settings.bankroll_engine_enabled else "DES"
        ),
        "Uso en seleccion: <b>{}ACTIVADO</b>".format(
            "" if settings.bankroll_use_for_selection else "DES"
        ),
        _DISCLAIMER,
    ]
    await send_html(update, "\n".join(lines))
