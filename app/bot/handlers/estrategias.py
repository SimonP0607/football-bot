"""Phase 13: Telegram handlers for /estrategias — strategy learning overview.

Commands:
  /estrategias           — resumen general
  /estrategias mejor     — top strategies by score
  /estrategias peor      — weak/dangerous strategies
  /estrategia <key>      — detail for a specific strategy key
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
    "\n<i>Las estrategias son analisis historico. "
    "No garantizan rendimiento futuro.</i>"
)

_REC_ICON = {
    "promote":           "++",
    "monitor":           "~>",
    "neutral":           " =",
    "reduce":            "--",
    "avoid":             "!!",
    "insufficient_sample": "?",
}


def _icon(rec: str | None) -> str:
    return _REC_ICON.get(rec or "", " ?")


def _pct(v: float | None, decimals: int = 1) -> str:
    if v is None:
        return "n/a"
    return f"{v * 100:+.{decimals}f}%"


def _fmt_score(s: float | None) -> str:
    if s is None:
        return "?"
    return f"{s:.0f}"


def _short_key(key: str | None, max_len: int = 50) -> str:
    if not key:
        return "-"
    return key[:max_len] + ("…" if len(key) > max_len else "")


def _build_strategy_profile_block(p: dict) -> str:
    key   = p.get("strategy_key") or "-"
    mkt   = p.get("market_key") or "-"
    lg    = p.get("league_name") or (f"Liga {p['league_id']}" if p.get("league_id") else "Global")
    n     = p.get("sample_size") or 0
    wins  = p.get("wins") or 0
    loss  = p.get("losses") or 0
    hr    = p.get("hit_rate")
    roi   = p.get("roi")
    clv   = p.get("avg_clv_percent")
    beat  = p.get("clv_beat_rate")
    score = p.get("strategy_score") or 0
    rec   = p.get("recommendation") or "-"
    icon  = _icon(rec)

    lines = [
        f"<b>[{icon}] Score {_fmt_score(score)}/100 — {rec.upper()}</b>",
        f"<code>{_short_key(key)}</code>",
        f"Mercado: <b>{mkt}</b>  Liga: {lg}",
        f"Muestra: <b>{n}</b> picks  ({wins}G / {loss}P)",
        f"Hit rate: <b>{_pct(hr)}</b>  ROI: <b>{_pct(roi)}</b>",
        f"CLV avg: <b>{clv:+.2f}</b>  Beat rate: <b>{_pct(beat)}</b>"
        if clv is not None else "CLV: sin datos",
    ]
    return "\n".join(lines)


@require_auth
async def estrategias_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/estrategias [mejor|peor] — strategy learning overview."""
    logger.info("/estrategias solicitado por user_id=%s", update.effective_user.id)

    args = context.args or []
    subcommand = args[0].lower() if args else ""

    if subcommand in ("mejor", "mejores", "best", "top"):
        await _handle_best(update, context)
    elif subcommand in ("peor", "peores", "worst", "weak", "debil", "debiles"):
        await _handle_worst(update, context)
    else:
        await _handle_summary(update, context)


@require_auth
async def estrategia_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/estrategia <strategy_key> — detail for a specific strategy."""
    logger.info("/estrategia solicitado por user_id=%s", update.effective_user.id)

    args = context.args or []
    key = " ".join(args).strip()

    if not key:
        await update.message.reply_html(
            "Uso: <code>/estrategia &lt;strategy_key&gt;</code>\n\n"
            "Ejemplo: <code>/estrategia OU25|league_39|odds_1.90_2.50|...</code>\n\n"
            "Usa /estrategias para ver las claves disponibles."
        )
        return

    conn = _get_conn()
    if conn is None:
        await send_html(update.message, "Base de datos local no disponible.")
        return

    try:
        from app.data.local.strategy_learning_repo import get_strategy_by_key
        p = get_strategy_by_key(conn, key)
    except Exception as exc:
        logger.error("/estrategia: error — %s", exc)
        await send_html(update.message, "Error al buscar la estrategia.")
        return

    if not p:
        await send_html(
            update.message,
            f"No se encontro la estrategia: <code>{key[:80]}</code>\n\n"
            "Usa /estrategias para ver las disponibles."
        )
        return

    text = _build_strategy_detail(p, conn)
    await send_html(update.message, text)


# ── Sub-handlers ──────────────────────────────────────────────────────────────

async def _handle_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = _get_conn()

    lines = ["<b>Aprendizaje Estrategico</b>", ""]

    if not settings.strategy_learning_enabled:
        lines.append("Estado: <b>INACTIVO</b>")
        lines.append("")
        lines.append("Para activar:")
        lines.append("  <code>STRATEGY_LEARNING_ENABLED=true</code>")
        lines.append("  <code>python scripts/build_strategy_learning.py --execute</code>")
        await send_html(update.message, "\n".join(lines))
        return

    if conn is None:
        lines.append("Base de datos local no disponible.")
        await send_html(update.message, "\n".join(lines))
        return

    try:
        from app.data.local.strategy_learning_repo import (
            get_learning_summary, get_best_strategies, get_weak_strategies,
            get_strategy_adjustments,
        )
        summary = get_learning_summary(conn)

        total_p = summary.get("total_profiles", 0)
        total_a = summary.get("total_annotations", 0)
        with_r  = summary.get("annotated_with_result", 0)
        with_c  = summary.get("annotated_with_clv", 0)
        pending = summary.get("pending_adjustments", 0)

        lines.append(f"Picks anotados: <b>{total_a}</b>  (con settlement: {with_r} · con CLV: {with_c})")
        lines.append(f"Perfiles de estrategia: <b>{total_p}</b>")

        rec_counts = summary.get("recommendation_counts") or {}
        if rec_counts:
            rec_parts = []
            for rec in ("promote", "monitor", "neutral", "reduce", "avoid", "insufficient_sample"):
                n = rec_counts.get(rec, 0)
                if n:
                    rec_parts.append(f"{_icon(rec)}{rec[:6]}:{n}")
            if rec_parts:
                lines.append("Dist: " + "  ".join(rec_parts))

        lines.append("")

        best = get_best_strategies(conn, limit=3)
        if best:
            lines.append("<b>Mejores estrategias:</b>")
            for p in best:
                icon = _icon(p.get("recommendation"))
                sc   = _fmt_score(p.get("strategy_score"))
                roi  = _pct(p.get("roi"))
                mkt  = p.get("market_key") or "-"
                lines.append(f"  [{icon}] <b>{sc}</b>  {mkt}  ROI {roi}")
        else:
            lines.append("Sin estrategias aun. Ejecuta:")
            lines.append("  <code>python scripts/build_strategy_learning.py --execute</code>")

        lines.append("")
        weak = get_weak_strategies(conn, limit=3)
        if weak:
            lines.append("<b>Estrategias debiles:</b>")
            for p in weak:
                icon = _icon(p.get("recommendation"))
                sc   = _fmt_score(p.get("strategy_score"))
                roi  = _pct(p.get("roi"))
                mkt  = p.get("market_key") or "-"
                lines.append(f"  [{icon}] <b>{sc}</b>  {mkt}  ROI {roi}")

        if pending:
            lines.append("")
            lines.append(f"Recomendaciones pendientes: <b>{pending}</b>")

        lines.append("")
        lines.append("/estrategias mejor  — top estrategias")
        lines.append("/estrategias peor   — estrategias debiles")

    except Exception as exc:
        logger.error("/estrategias summary error — %s", exc)
        lines.append("Error al cargar estrategias. Revisa los logs.")

    lines.append(_DISCLAIMER)
    await send_html(update.message, "\n".join(lines))


async def _handle_best(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = _get_conn()
    lines = ["<b>Mejores estrategias</b>", ""]

    if conn is None:
        lines.append("Base de datos local no disponible.")
        await send_html(update.message, "\n".join(lines))
        return

    try:
        from app.data.local.strategy_learning_repo import get_best_strategies
        profiles = get_best_strategies(conn, limit=8)

        if not profiles:
            lines.append("Sin estrategias con muestra suficiente.")
            lines.append("Ejecuta: <code>python scripts/build_strategy_learning.py --execute</code>")
        else:
            for p in profiles:
                lines.append(_build_strategy_profile_block(p))
                lines.append("")
    except Exception as exc:
        logger.error("/estrategias mejor error — %s", exc)
        lines.append("Error al cargar estrategias.")

    lines.append(_DISCLAIMER)
    await send_html(update.message, "\n".join(lines))


async def _handle_worst(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = _get_conn()
    lines = ["<b>Estrategias debiles / peligrosas</b>", ""]

    if conn is None:
        lines.append("Base de datos local no disponible.")
        await send_html(update.message, "\n".join(lines))
        return

    try:
        from app.data.local.strategy_learning_repo import get_weak_strategies
        profiles = get_weak_strategies(conn, limit=8)

        if not profiles:
            lines.append("Sin estrategias con muestra suficiente.")
        else:
            for p in profiles:
                lines.append(_build_strategy_profile_block(p))
                lines.append("")

            lines.append(
                "<i>Estrategias con score bajo indican condiciones donde el "
                "sistema ha tenido peor rendimiento historico.</i>"
            )
    except Exception as exc:
        logger.error("/estrategias peor error — %s", exc)
        lines.append("Error al cargar estrategias.")

    lines.append(_DISCLAIMER)
    await send_html(update.message, "\n".join(lines))


# ── Strategy detail block ─────────────────────────────────────────────────────

def _build_strategy_detail(p: dict, conn) -> str:
    lines = [f"<b>Estrategia — Detalle</b>", ""]
    lines.append(f"<code>{p.get('strategy_key', '-')}</code>")
    lines.append("")

    mkt   = p.get("market_key") or "-"
    lg    = p.get("league_name") or (f"Liga {p['league_id']}" if p.get("league_id") else "Global")
    scope = p.get("scope") or "-"
    lines.append(f"Mercado: <b>{mkt}</b>   Liga: {lg}  ({scope})")
    lines.append("")

    n     = p.get("sample_size") or 0
    wins  = p.get("wins") or 0
    loss  = p.get("losses") or 0
    voids = p.get("voids") or 0
    lines.append(f"Muestra: <b>{n}</b> picks  ({wins}G / {loss}P / {voids}V)")

    hr   = p.get("hit_rate")
    roi  = p.get("roi")
    clv  = p.get("avg_clv_percent")
    beat = p.get("clv_beat_rate")
    stab = p.get("stability_score")
    sc   = p.get("strategy_score") or 0
    rec  = p.get("recommendation") or "-"

    lines.append(f"Hit rate:    <b>{_pct(hr)}</b>")
    lines.append(f"ROI:         <b>{_pct(roi)}</b>")
    lines.append(f"CLV promedio:<b>{clv:+.2f}</b>" if clv is not None else "CLV promedio: sin datos")
    lines.append(f"CLV beat:    <b>{_pct(beat)}</b>")
    lines.append(f"Estabilidad: <b>{stab:.2f}</b>" if stab is not None else "Estabilidad: n/a")
    lines.append("")
    lines.append(f"Score:       <b>{sc:.1f} / 100</b>")
    lines.append(f"Recomend.:   <b>{_icon(rec)} {rec.upper()}</b>")
    lines.append("")

    # Buckets
    lines.append("<b>Rangos:</b>")
    for bkt in ("odds_bucket", "edge_bucket", "confidence_bucket", "clv_bucket"):
        v = p.get(bkt)
        if v:
            lines.append(f"  {bkt.replace('_bucket', ''):14s} {v}")

    # Recent annotations
    try:
        rows = conn.execute(
            """
            SELECT learning_label, result_status, clv_percent, beat_closing_line
            FROM pick_learning_annotations
            WHERE strategy_key = ?
            ORDER BY created_at DESC LIMIT 8
            """,
            [p.get("strategy_key")],
        ).fetchall()
        if rows:
            lines.append("")
            lines.append("<b>Picks recientes:</b>")
            for lbl, rs, clv_v, beat_v in rows:
                clv_s  = f"{clv_v:+.2f}" if clv_v is not None else "  n/a"
                beat_s = "si" if beat_v else "no"
                lines.append(f"  {lbl:18s} {rs or '?':6s}  clv={clv_s}  beat={beat_s}")
    except Exception:
        pass

    lines.append(_DISCLAIMER)
    return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_conn():
    try:
        from app.data.local.duckdb_client import get_local_db, init_schema
        conn = get_local_db()
        init_schema(conn)
        return conn
    except Exception as exc:
        logger.debug("estrategias: DuckDB unavailable — %s", exc)
        return None


# ── Public: strategy label for /top ──────────────────────────────────────────

def get_strategy_label(strategy_rec: str | None, strategy_score: float | None,
                       strategy_sample: int | None) -> str | None:
    """Return a short label to show alongside a pick in /top.

    Returns None if no meaningful strategy data exists.
    """
    if strategy_rec is None or strategy_score is None:
        return None
    if strategy_rec == "insufficient_sample" or (strategy_sample or 0) < 10:
        return "Muestra insuficiente"
    if strategy_rec == "promote":
        return f"Estrategia fuerte ({strategy_score:.0f}/100)"
    if strategy_rec == "monitor":
        return f"En observacion ({strategy_score:.0f}/100)"
    if strategy_rec == "reduce":
        return f"Riesgo historico ({strategy_score:.0f}/100)"
    if strategy_rec == "avoid":
        return f"Evitar historicamente ({strategy_score:.0f}/100)"
    return None
