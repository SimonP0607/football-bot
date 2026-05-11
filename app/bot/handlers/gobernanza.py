"""Phase 15: Telegram handlers for model governance.

Commands: /gobernanza, /experimentos, /activar
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.handlers.auth import require_auth

logger = logging.getLogger(__name__)

_REC_ICON = {
    "DO_NOT_ACTIVATE":           "✗",
    "OBSERVE_MORE":              "◎",
    "SAFE_TO_TEST_SHADOW":       "◑",
    "SAFE_TO_TEST_ASSIST":       "◕",
    "SAFE_TO_USE_FOR_SELECTION": "●",
}

_MODULE_FLAG = {
    "strategy_learning": "STRATEGY_LEARNING_ENABLED",
    "bankroll":          "BANKROLL_ENGINE_ENABLED",
    "market_clv":        "MARKET_INTELLIGENCE_ENABLED",
    "parlay":            "PARLAY_ENGINE_ENABLED",
    "live_monitoring":   "LIVE_TRACKING_ENABLED",
}


def _pct(v) -> str:
    if v is None:
        return "n/a"
    return f"{v * 100:+.1f}%"


@require_auth
async def gobernanza_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show high-level governance dashboard."""
    from app.core.config import settings
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.data.local.model_governance_repo import (
        get_activation_recommendations,
        get_governance_summary,
    )

    try:
        conn = get_local_db()
        init_schema(conn)

        summary = get_governance_summary(conn)
        recs = get_activation_recommendations(conn)

        lines = ["<b>Model Governance — Resumen</b>\n"]

        # Status
        enabled = settings.model_governance_enabled
        lines.append(f"  Estado: {'✓ activo' if enabled else '○ pasivo (shadow)'}")
        lines.append(f"  Auto-activate: {'⚠ SÍ' if settings.model_governance_auto_activate else '✗ NUNCA'}\n")

        # Experiments
        exp_counts = summary.get("experiments") or {}
        active_n = exp_counts.get("active", 0)
        lines.append(f"  Experimentos activos: <b>{active_n}</b>")

        audit = summary.get("audit_7d") or {}
        lines.append(f"  Audits (7d): {audit.get('total', 0)}  cambiados: {audit.get('changed', 0)}\n")

        # Recommendations
        if recs:
            lines.append("<b>Recomendaciones por módulo:</b>")
            for rec in recs:
                icon = _REC_ICON.get(rec["recommendation"], "?")
                gp = rec.get("gates_passed", 0)
                gt = rec.get("gates_total", 0)
                gates_str = f"{gp}/{gt}" if gt else "n/a"
                lines.append(
                    f"  {icon} <b>{rec['module']}</b>  {rec['recommendation']}  gates={gates_str}"
                )
        else:
            lines.append("  Sin datos — ejecuta <code>run_experiment_lab.py --execute</code>")

        lines.append("\n  /experimentos — detalle por variante")
        lines.append("  /activar — instrucciones de activación segura")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    except Exception as exc:
        logger.exception("/gobernanza error: %s", exc)
        await update.message.reply_text(
            "⚠ Error al consultar gobernanza. Verifica DuckDB.", parse_mode="HTML"
        )


@require_auth
async def experimentos_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show experiment results per variant."""
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.data.local.model_governance_repo import get_experiment_results

    try:
        conn = get_local_db()
        init_schema(conn)

        results = get_experiment_results(conn, days_window=30)

        if not results:
            await update.message.reply_text(
                "Sin datos de experimentos.\nEjecuta: <code>python scripts/run_experiment_lab.py --execute</code>",
                parse_mode="HTML",
            )
            return

        lines = ["<b>Experimentos — últimos 30d</b>\n"]
        for r in results:
            key = r.get("experiment_key", "?")
            rec = r.get("recommendation", "DO_NOT_ACTIVATE")
            icon = _REC_ICON.get(rec, "?")
            n = r.get("sample_size", 0)
            roi = r.get("roi")
            lift = r.get("lift_vs_baseline")
            gp = r.get("gates_passed", 0)
            gt = r.get("gates_total", 0)
            lines.append(
                f"  {icon} <b>{key}</b>\n"
                f"     n={n}  ROI={_pct(roi)}  lift={_pct(lift)}  gates={gp}/{gt}"
            )

        lines.append("\n  /gobernanza — resumen global")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    except Exception as exc:
        logger.exception("/experimentos error: %s", exc)
        await update.message.reply_text(
            "⚠ Error al consultar experimentos.", parse_mode="HTML"
        )


@require_auth
async def activar_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Explain safe activation process — never activates automatically."""
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.data.local.model_governance_repo import get_activation_recommendations

    try:
        conn = get_local_db()
        init_schema(conn)
        recs = get_activation_recommendations(conn)
    except Exception:
        recs = []

    lines = [
        "<b>Activación Segura — Instrucciones</b>\n",
        "⚠ <b>IMPORTANTE:</b> Ningún módulo se activa automáticamente.",
        "Toda activación requiere cambio manual en .env + reinicio.\n",
        "<b>Estado actual:</b>",
    ]

    if recs:
        for rec in recs:
            icon = _REC_ICON.get(rec["recommendation"], "?")
            module = rec["module"]
            flag = _MODULE_FLAG.get(module, "?")
            recommendation = rec["recommendation"]
            gp = rec.get("gates_passed", 0)
            gt = rec.get("gates_total", 0)
            lines.append(f"\n  {icon} <b>{module}</b>  ({recommendation})")
            if gt:
                lines.append(f"     Gates: {gp}/{gt}")
            blocking = rec.get("blocking_reason")
            if blocking:
                lines.append(f"     Bloqueado: {blocking}")
            if recommendation in ("SAFE_TO_TEST_SHADOW", "SAFE_TO_TEST_ASSIST",
                                  "SAFE_TO_USE_FOR_SELECTION"):
                lines.append(f"     → Activa: <code>{flag}=true</code> en .env")
    else:
        lines.append("  Sin recomendaciones — ejecuta el experiment lab primero.")

    lines.append(
        "\n<b>Orden recomendado:</b>\n"
        "  1. shadow (solo observa, no cambia picks)\n"
        "  2. assist (informa pero no decide)\n"
        "  3. selection (participa en selección)\n\n"
        "  Más info: <code>python scripts/report_activation_readiness.py</code>"
    )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
