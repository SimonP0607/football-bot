"""Handler for /live — show currently tracked live fixtures and pick states."""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.utils import esc, send_html

logger = logging.getLogger(__name__)

_STATE_ICON = {"winning": "✅", "losing": "❌", "open": "⬜"}
_STATUS_LABEL = {
    "1H": "1T", "HT": "ET", "2H": "2T",
    "ET": "Pról", "BT": "Desc", "P": "Pens",
    "FT": "FT", "AET": "FT (pról)", "PEN": "FT (pens)",
    "NS": "No iniciado", "PST": "Aplazado",
}
_MKT_LABEL = {"1X2": "1X2", "OU25": "O/U 2.5", "BTTS": "BTTS", "DC": "DC"}


@require_auth
async def live_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/live — Display live fixture tracking and pick states."""
    logger.info("/live solicitado por user_id=%s", update.effective_user.id)

    try:
        text = _build_text()
    except Exception as exc:
        logger.error("/live: error — %s", exc, exc_info=True)
        text = "Error al obtener datos live. Usa /estado para diagnosticar."

    await send_html(update.message, text)


def _build_text() -> str:
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.services.live_monitor_service import get_live_summary

    conn = get_local_db()
    init_schema(conn)
    summary = get_live_summary(conn)

    lines: list[str] = ["<b>Live Monitoring</b>", ""]

    if not summary:
        lines.append("Sin partidos rastreados en este momento.")
        lines.append("")
        lines.append("El monitor live actualiza datos al ejecutar:")
        lines.append("<code>python scripts/live_monitor.py --execute</code>")
        return "\n".join(lines)

    # Load team names from Supabase
    team_map: dict[int, str] = {}
    try:
        from app.data.repositories.supabase_client import get_supabase
        pids: set[int] = set()
        for snap in summary:
            pass  # we don't store team IDs in live snapshots
        # Try to load via fix lookup
    except Exception:
        pass

    _FINISHED = {"FT", "AET", "PEN", "WO"}
    sep = "─" * 30

    live_count  = sum(1 for s in summary if not s.get("is_finished"))
    fin_count   = sum(1 for s in summary if s.get("is_finished"))
    lines.append(f"En juego: <b>{live_count}</b>  ·  Finalizados: <b>{fin_count}</b>")
    lines.append("")

    for snap in sorted(summary, key=lambda s: (s.get("is_finished", False), s.get("provider_fixture_id", 0))):
        pfid      = snap["provider_fixture_id"]
        status    = snap.get("status_short", "?")
        elapsed   = snap.get("status_elapsed")
        gh        = snap.get("goals_home", 0) or 0
        ga        = snap.get("goals_away", 0) or 0
        picks     = snap.get("picks", [])
        is_fin    = snap.get("is_finished", False)

        status_label = _STATUS_LABEL.get(status, status)
        elapsed_str  = f" {elapsed}'" if elapsed and not is_fin else ""
        score_str    = f"{gh}–{ga}"

        lines.append(sep)
        lines.append(
            f"Fixture <code>{pfid}</code>  "
            f"<b>{score_str}</b>  [{status_label}{elapsed_str}]"
        )

        if not picks:
            lines.append("  Sin picks rastreados")
        else:
            for pk in picks:
                icon  = _STATE_ICON.get(pk.get("live_state", "open"), "❓")
                mkt   = _MKT_LABEL.get(pk.get("market_key", ""), pk.get("market_key", "?"))
                sel   = esc(pk.get("selection", "?"))
                state = pk.get("live_state", "open").upper()
                lines.append(f"  {icon} {mkt}/{sel}  <i>{state}</i>")

    lines.append("")
    lines.append("Actualizar: <code>python scripts/live_monitor.py --execute</code>")
    return "\n".join(lines)
