"""Handler for /parlay — Smart Parlay Engine bot commands.

Subcommands:
  /parlay          — best parlay of each type today (2, 3, 4 legs)
  /parlay 2        — best 2-leg parlays
  /parlay 3        — best 3-leg parlays
  /parlay 4        — best 4-leg parlays
  /parlay riesgo   — explain risk & correlation rules
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.utils import esc, send_html

logger = logging.getLogger(__name__)

_MKT_LABEL = {"1X2": "1X2", "OU25": "O/U 2.5", "BTTS": "BTTS", "DC": "DC"}
_TYPE_ICON = {"conservadora": "🛡", "balanceada": "⚖️", "agresiva": "🔥"}
_TYPE_LABEL = {"conservadora": "Conservadora (2 legs)", "balanceada": "Balanceada (3 legs)", "agresiva": "Agresiva (4 legs)"}


@require_auth
async def parlay_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/parlay [2|3|4|riesgo] — Smart parlay engine."""
    logger.info("/parlay solicitado por user_id=%s args=%s", update.effective_user.id, context.args)

    args = context.args or []
    arg = args[0].lower() if args else ""

    from app.bot.ui.keyboard import parlay_keyboard, back_home_keyboard

    try:
        if arg == "riesgo":
            text = _build_riesgo_text()
            kb = back_home_keyboard()
        elif arg in ("2", "3", "4"):
            text = _build_legs_text(int(arg))
            kb = parlay_keyboard()
        else:
            text = _build_overview_text()
            kb = parlay_keyboard()
    except Exception as exc:
        logger.error("/parlay: error — %s", exc, exc_info=True)
        text = "Error al obtener parlays. Usa /estado para diagnosticar."
        kb = back_home_keyboard()

    try:
        await update.message.reply_html(text, reply_markup=kb)
    except Exception:
        await send_html(update.message, text)


def _get_conn():
    from app.data.local.duckdb_client import get_local_db, init_schema
    conn = get_local_db()
    init_schema(conn)
    return conn


def _format_parlay_block(parlay: dict, show_header: bool = True) -> list[str]:
    """Format a single parlay into display lines."""
    lines: list[str] = []

    ptype     = parlay.get("parlay_type", "")
    legs_count = parlay.get("legs_count", 0)
    total_odds = parlay.get("total_odds") or 0.0
    joint_prob = parlay.get("joint_probability") or 0.0
    edge       = parlay.get("edge") or 0.0
    ev         = parlay.get("ev") or 0.0
    risk       = parlay.get("risk_score") or 0.0
    conf       = parlay.get("confidence_score") or 0.0
    corr       = parlay.get("correlation_score") or 0.0
    legs       = parlay.get("legs") or []

    if show_header:
        icon  = _TYPE_ICON.get(ptype, "📋")
        label = _TYPE_LABEL.get(ptype, f"{legs_count} legs")
        lines.append(f"{icon} <b>{label}</b>")

    # Metrics row
    lines.append(
        f"Cuota: <b>{total_odds:.2f}x</b>  "
        f"Prob: <b>{joint_prob*100:.1f}%</b>  "
        f"Edge: <b>{edge*100:+.1f}%</b>  "
        f"EV: <b>{ev*100:+.1f}%</b>"
    )
    lines.append(
        f"Riesgo: <b>{risk*100:.0f}%</b>  "
        f"Correlación: <b>{corr*100:.0f}%</b>  "
        f"Confianza: <b>{conf*100:.0f}%</b>"
    )

    # Legs
    for i, leg in enumerate(legs, 1):
        mkt   = _MKT_LABEL.get(leg.get("market_key", ""), leg.get("market_key", "?"))
        sel   = esc(leg.get("selection", "?"))
        odds  = leg.get("odds") or 0.0
        edge_l = (leg.get("edge") or 0.0) * 100
        fid   = leg.get("fixture_id") or leg.get("provider_fixture_id") or "?"
        lid   = leg.get("league_id") or "?"
        lines.append(
            f"  {i}. <b>{mkt}/{sel}</b> @ {odds:.2f}  "
            f"edge {edge_l:+.1f}%  "
            f"<i>fix {esc(fid)} · liga {esc(lid)}</i>"
        )

    return lines


def _build_overview_text() -> str:
    conn = _get_conn()
    from app.services.parlay_engine_service import get_best_parlays_today

    lines: list[str] = ["<b>Parlays Recomendados Hoy</b>", ""]

    # One best per type
    found_any = False
    sep = "─" * 32
    for n, ptype in [(2, "conservadora"), (3, "balanceada"), (4, "agresiva")]:
        parlays = get_best_parlays_today(conn, n_legs=n)
        if parlays:
            lines.append(sep)
            lines.extend(_format_parlay_block(parlays[0]))
            found_any = True

    if not found_any:
        lines.append("Sin parlays recomendados para hoy.")
        lines.append("")
        lines.append("Genera parlays ejecutando:")
        lines.append("<code>python scripts/run_parlay_engine.py --execute</code>")
        return "\n".join(lines)

    lines.append("")
    lines.append("Ver por tipo: /parlay 2  /parlay 3  /parlay 4")
    lines.append("Reglas de riesgo: /parlay riesgo")
    return "\n".join(lines)


def _build_legs_text(n: int) -> str:
    conn = _get_conn()
    from app.services.parlay_engine_service import get_best_parlays_today

    _LABELS = {2: "2 Legs — Conservadora", 3: "3 Legs — Balanceada", 4: "4 Legs — Agresiva"}
    lines: list[str] = [f"<b>Parlays {_LABELS.get(n, f'{n} legs')} Hoy</b>", ""]

    parlays = get_best_parlays_today(conn, n_legs=n)
    if not parlays:
        lines.append(f"Sin parlays de {n} legs recomendados para hoy.")
        lines.append("")
        lines.append("Genera parlays ejecutando:")
        lines.append("<code>python scripts/run_parlay_engine.py --execute</code>")
        return "\n".join(lines)

    sep = "─" * 32
    for i, parlay in enumerate(parlays[:3], 1):
        lines.append(sep)
        lines.append(f"<b>#{i}</b>")
        lines.extend(_format_parlay_block(parlay, show_header=False))

    lines.append("")
    lines.append(f"Mostrando top {min(3, len(parlays))} de {len(parlays)} disponibles.")
    return "\n".join(lines)


def _build_riesgo_text() -> str:
    lines: list[str] = [
        "<b>Reglas de Riesgo y Correlación</b>",
        "",
        "El motor evalúa cada combinación usando estas reglas:",
        "",
        "<b>Alta severidad (rechazo automático):</b>",
        "• Mismo fixture: +80% correlación por par",
        "• Mismo equipo: +40% correlación por par",
        "• Over 2.5 + BTTS Sí mismo partido: +30%",
        "• Under 2.5 + BTTS No mismo partido: +30%",
        "",
        "<b>Severidad media:</b>",
        "• Local gana + Over mismo partido: +20%",
        "• Más de 2 picks en misma liga: +8% por pick extra",
        "",
        "<b>Severidad baja:</b>",
        "• Más del 50% de picks son Away: +10%",
        "• Picks repetidos en mismo mercado: +8%",
        "",
        "<b>Umbrales de recomendación:</b>",
        f"• Correlación máxima permitida: 45%",
        f"• Riesgo máximo permitido: 60%",
        f"• EV mínimo requerido: +2%",
        f"• Edge mínimo requerido: +3%",
        f"• Confianza mínima: 55%",
        "",
        "<b>Tipos de parlay:</b>",
        "🛡 Conservadora — 2 legs (menor riesgo, cuota moderada)",
        "⚖️ Balanceada — 3 legs (equilibrio riesgo/cuota)",
        "🔥 Agresiva — 4 legs (mayor cuota, mayor riesgo)",
        "",
        "Ver parlays: /parlay",
    ]
    return "\n".join(lines)
