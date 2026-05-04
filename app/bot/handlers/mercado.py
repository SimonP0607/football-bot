"""Phase 12: /mercado and /clv handlers — Market Intelligence.

/mercado <fixture_id>   — closing lines, movement signals and CLV for a fixture
/clv [--days N]         — aggregate CLV performance report
"""

from __future__ import annotations

import html
import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.utils import send_html

logger = logging.getLogger(__name__)

_DISCLAIMER = (
    "\n\n<i>ℹ️ Datos de mercado con fines analíticos. "
    "No son recomendaciones de apuesta.</i>"
)


def _format_movement(label: str) -> str:
    icons = {
        "steam_towards_selection": "🔥 Steam",
        "drift_against_selection": "📉 Drift",
        "stable": "➡️ Estable",
        "reverse_line_movement": "↩️ Reverse",
        "sharp_like_move": "🔪 Sharp",
        "no_data": "—",
    }
    return icons.get(label, label)


def _format_signal(signal_type: str) -> str:
    icons = {
        "steam": "🔥",
        "drift": "📉",
        "sharp_move": "🔪",
        "stable": "➡️",
        "no_liquidity": "💧",
        "reverse_line": "↩️",
    }
    return icons.get(signal_type, "•")


def _format_clv_result(result: str) -> str:
    icons = {
        "positive": "✅ +CLV",
        "neutral": "➡️ Neutro",
        "negative": "❌ -CLV",
        "no_data": "—",
    }
    return icons.get(result, result)


def _build_mercado_text(provider_fixture_id: int, summary: dict) -> str:
    if not summary:
        return (
            f"No hay datos de mercado para el fixture <code>{provider_fixture_id}</code>.\n\n"
            "Asegúrate de haber ejecutado la sincronización de cuotas "
            "(<code>MARKET_INTELLIGENCE_ENABLED=true</code>)."
        )

    lines: list[str] = [
        f"<b>📈 Mercado — Fixture {provider_fixture_id}</b>",
    ]

    # Closing lines grouped by market
    closing_lines = summary.get("closing_lines", [])
    if closing_lines:
        lines.append("\n<b>Líneas de cierre</b>")
        for cl in closing_lines[:12]:
            mk = html.escape(cl.get("market_key", ""))
            sel = html.escape(cl.get("selection", ""))
            open_o = cl.get("opening_odds")
            close_o = cl.get("closing_odds")
            label = _format_movement(cl.get("movement_label", "no_data"))
            delta = cl.get("implied_delta")
            open_str = f"{open_o:.2f}" if open_o else "N/A"
            close_str = f"{close_o:.2f}" if close_o else "N/A"
            delta_str = f" (Δ{delta*100:+.1f}pp)" if delta is not None else ""
            lines.append(
                f"  {mk} · {sel}: {open_str} → {close_str}{delta_str} {label}"
            )

    # Active signals
    signals = summary.get("signals", [])
    active = [s for s in signals if s.get("signal_type") not in ("stable", None)]
    if active:
        lines.append("\n<b>Señales detectadas</b>")
        for sig in active[:6]:
            icon = _format_signal(sig.get("signal_type", ""))
            mk = html.escape(sig.get("market_key", ""))
            sel = html.escape(sig.get("selection", ""))
            delta = sig.get("implied_delta")
            delta_str = f" Δ{delta*100:+.1f}pp" if delta is not None else ""
            reason = html.escape(sig.get("reason_text", ""))
            lines.append(f"  {icon} {mk} · {sel}{delta_str}")
            if reason:
                lines.append(f"    → {reason}")

    flags = []
    if summary.get("has_steam"):
        flags.append("🔥 Steam activo")
    if summary.get("has_reverse_line"):
        flags.append("↩️ Reverse Line Movement")
    if summary.get("has_sharp_move"):
        flags.append("🔪 Sharp Money")
    if flags:
        lines.append("\n<b>Alertas</b>: " + " · ".join(flags))

    lines.append(_DISCLAIMER)
    return "\n".join(lines)


def _build_clv_text(summary: dict, days: int) -> str:
    if not summary or summary.get("total", 0) == 0:
        return (
            f"No hay datos de CLV para los últimos {days} días.\n\n"
            "El CLV se calcula después de que los partidos terminan "
            "comparando los momios de los picks contra la línea de cierre."
        )

    total = summary.get("total", 0)
    beat = summary.get("beat_count", 0)
    avg_clv = summary.get("avg_clv")
    beat_rate = summary.get("beat_rate", 0.0)

    avg_str = f"{avg_clv*100:+.2f}pp" if avg_clv is not None else "N/A"
    beat_pct = f"{beat_rate*100:.1f}%"

    if beat_rate >= 0.55:
        assessment = "✅ Superamos la línea consistentemente — señal positiva de EV."
    elif beat_rate >= 0.45:
        assessment = "➡️ En línea con el mercado — tracking neutral."
    else:
        assessment = "⚠️ Por debajo de la línea — revisar selección de momios."

    lines = [
        f"<b>📊 CLV Report — {days} días</b>",
        "",
        f"  Picks con CLV     : {total}",
        f"  Beat closing line : {beat} ({beat_pct})",
        f"  CLV promedio      : {avg_str}",
        "",
        f"  ✅ Positivo  : {summary.get('positive_count', 0)}",
        f"  ➡️ Neutro    : {summary.get('neutral_count', 0)}",
        f"  ❌ Negativo  : {summary.get('negative_count', 0)}",
        f"  — Sin datos  : {summary.get('no_data_count', 0)}",
        "",
        f"  <b>Evaluación</b>: {assessment}",
        _DISCLAIMER,
    ]
    return "\n".join(lines)


@require_auth
async def mercado_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/mercado <fixture_id> — Market intelligence for a fixture."""
    logger.info("/mercado solicitado por user_id=%s", update.effective_user.id)

    args = context.args or []
    query = " ".join(args).strip()

    if not query or not query.isdigit():
        await update.message.reply_text(
            "Uso: <code>/mercado &lt;fixture_id&gt;</code>\n\n"
            "Ejemplo: <code>/mercado 1035066</code>\n\n"
            "Muestra closing lines, señales de movimiento y CLV para el partido.",
            parse_mode="HTML",
        )
        return

    provider_fixture_id = int(query)

    try:
        from app.data.local.duckdb_client import get_local_db, init_schema
        from app.services.market_intelligence_service import get_fixture_market_summary

        conn = get_local_db()
        init_schema(conn)
        summary = get_fixture_market_summary(conn, provider_fixture_id)
        text = _build_mercado_text(provider_fixture_id, summary)
    except Exception as exc:
        logger.error("/mercado: error — %s", exc)
        text = "Error al cargar datos de mercado. Verifica los logs."

    await send_html(update.message, text)


@require_auth
async def clv_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/clv [días] — Aggregate CLV performance report."""
    logger.info("/clv solicitado por user_id=%s", update.effective_user.id)

    args = context.args or []
    days = 30
    if args and args[0].isdigit():
        days = max(1, min(int(args[0]), 365))

    try:
        from app.data.local.duckdb_client import get_local_db, init_schema
        from app.services.market_intelligence_service import get_clv_report

        conn = get_local_db()
        init_schema(conn)
        summary = get_clv_report(conn, days=days)
        text = _build_clv_text(summary, days)
    except Exception as exc:
        logger.error("/clv: error — %s", exc)
        text = "Error al cargar reporte de CLV. Verifica los logs."

    await send_html(update.message, text)
