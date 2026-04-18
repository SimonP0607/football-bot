"""Handler for /ligas — shows active leagues and their season/coverage status."""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.middleware.db_guard import ensure_db_ready
from app.core.config import settings
from app.data.repositories.fixture_repo import get_active_leagues

logger = logging.getLogger(__name__)


@require_auth
async def ligas_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ligas — List active leagues with season and coverage info."""
    logger.info("/ligas solicitado por user_id=%s", update.effective_user.id)

    if not await ensure_db_ready(update, context):
        return

    try:
        leagues = get_active_leagues()
        configured_ids = set(settings.league_ids_list)
        seasons_map = settings.league_seasons_map
        text = _format_ligas(leagues, configured_ids, seasons_map)
    except Exception as exc:
        logger.error("/ligas: error — %s", exc, exc_info=True)
        text = "Error al obtener ligas. Usa /estado para diagnosticar el sistema."

    await update.message.reply_text(text, parse_mode="HTML")


def _format_ligas(
    leagues: list[dict],
    configured_ids: set[int],
    seasons_map: dict[int, int],
) -> str:
    if not leagues:
        return (
            "No hay ligas registradas en la base de datos.\n\n"
            "Ejecuta la migración 003 y luego:\n"
            "  <code>python scripts/sync_reference.py</code>\n"
            "  <code>python scripts/sync_today.py</code>"
        )

    lines = ["<b>Ligas activas</b>\n"]
    for lg in leagues:
        pid = lg.get("provider_league_id")
        name = lg.get("name", "?")
        country = lg.get("country", "")
        season = lg.get("season", "?")
        is_configured = pid in configured_ids
        coverage = lg.get("coverage") or {}

        # Coverage indicators
        cov_parts = []
        if coverage.get("standings"):
            cov_parts.append("standings")
        if coverage.get("injuries"):
            cov_parts.append("injuries")
        if coverage.get("predictions"):
            cov_parts.append("predictions")
        if coverage.get("odds"):
            cov_parts.append("odds")
        cov_str = ", ".join(cov_parts) if cov_parts else "sin coverage conocida"

        configured_mark = "✓" if is_configured else "○"
        lines.append(
            f"{configured_mark} <b>{name}</b> ({country})\n"
            f"   ID: <code>{pid}</code> · Season: <b>{season}</b>\n"
            f"   Coverage: {cov_str}"
        )

    lines.append(
        "\n<i>✓ = configurada en DEFAULT_LEAGUE_IDS · ○ = en BD pero no en sync</i>"
    )
    return "\n".join(lines)
