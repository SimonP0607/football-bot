"""Handler for /seguimiento — track a specific fixture's live pick states.

Usage:
  /seguimiento 1060362   — show live state for a specific provider fixture ID
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.utils import esc, send_html

logger = logging.getLogger(__name__)

_STATE_ICON  = {"winning": "✅", "losing": "❌", "open": "⬜"}
_STATE_LABEL = {"winning": "GANANDO", "losing": "PERDIENDO", "open": "ABIERTO"}
_MKT_LABEL   = {"1X2": "1X2", "OU25": "O/U 2.5", "BTTS": "BTTS", "DC": "DC"}
_STATUS_LABEL = {
    "1H": "1T", "HT": "ET", "2H": "2T",
    "ET": "Pról", "BT": "Desc", "P": "Pens",
    "FT": "FT", "AET": "FT (pról)", "PEN": "FT (pens)",
    "NS": "No iniciado",
}


@require_auth
async def seguimiento_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/seguimiento <fixture_id> — Show live tracking for a specific fixture."""
    logger.info("/seguimiento solicitado por user_id=%s", update.effective_user.id)

    args = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text(
            "Uso: <code>/seguimiento &lt;fixture_id&gt;</code>\n\n"
            "Ejemplo: <code>/seguimiento 1060362</code>",
            parse_mode="HTML",
        )
        return

    prov_fid = int(args[0])

    try:
        text = _build_text(prov_fid)
    except Exception as exc:
        logger.error("/seguimiento: error fixture=%s — %s", prov_fid, exc, exc_info=True)
        text = "Error al obtener datos de seguimiento."

    await send_html(update.message, text)


def _build_text(prov_fid: int) -> str:
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.services.live_monitor_service import get_fixture_live_state

    conn = get_local_db()
    init_schema(conn)
    state = get_fixture_live_state(conn, prov_fid)

    lines: list[str] = [f"<b>Seguimiento — Fixture {prov_fid}</b>", ""]

    if state is None:
        lines.append("Sin datos de monitoreo para este fixture.")
        lines.append("")
        lines.append("Ejecuta el monitor live para capturar datos:")
        lines.append(f"<code>python scripts/live_monitor.py --fixture {prov_fid} --execute</code>")
        return "\n".join(lines)

    status   = state.get("status_short", "?")
    elapsed  = state.get("status_elapsed")
    gh       = state.get("goals_home", 0) or 0
    ga       = state.get("goals_away", 0) or 0
    is_fin   = state.get("is_finished", False)
    picks    = state.get("picks", [])
    last_upd = (state.get("last_seen_at") or "")[:16].replace("T", " ")

    status_label = _STATUS_LABEL.get(status, status)
    elapsed_str  = f" {elapsed}'" if elapsed and not is_fin else ""

    lines.append(f"Marcador: <b>{gh}–{ga}</b>  [{status_label}{elapsed_str}]")
    if last_upd:
        lines.append(f"Última actualización: {esc(last_upd)} UTC")
    lines.append("")

    if not picks:
        lines.append("Sin picks rastreados para este fixture.")
    else:
        lines.append("<b>Picks rastreados:</b>")
        sep = "─" * 28
        for pk in picks:
            icon  = _STATE_ICON.get(pk.get("live_state", "open"), "❓")
            label = _STATE_LABEL.get(pk.get("live_state", "open"), pk.get("live_state", "?"))
            mkt   = _MKT_LABEL.get(pk.get("market_key", ""), pk.get("market_key", "?"))
            sel   = esc(pk.get("selection", "?"))
            upd   = (pk.get("last_updated_at") or "")[:16].replace("T", " ")
            lines.append(sep)
            lines.append(f"  {icon} <b>{mkt}/{sel}</b>  <i>{label}</i>")
            if upd:
                lines.append(f"  Actualizado: {esc(upd)} UTC")

    lines.append("")
    if is_fin:
        lines.append("Partido finalizado. Liquida con:")
        lines.append("<code>python scripts/settle_results.py</code>")
    else:
        lines.append("Actualizar: <code>python scripts/live_monitor.py --execute</code>")

    return "\n".join(lines)
