"""Handler for /ligas — shows active leagues and their season/coverage/tier status."""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.middleware.db_guard import ensure_db_ready
from app.data.repositories.fixture_repo import get_active_leagues

logger = logging.getLogger(__name__)

_TIER_LABEL = {
    "tier_1_daily": "T1 diario",
    "tier_2_matchday": "T2 jornada",
    "tier_3_light": "T3 ligero",
}


@require_auth
async def ligas_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ligas — List active leagues with season, coverage, and sync tier."""
    logger.info("/ligas solicitado por user_id=%s", update.effective_user.id)

    if not await ensure_db_ready(update, context):
        return

    try:
        leagues = get_active_leagues()
        text = _format_ligas(leagues)
    except Exception as exc:
        logger.error("/ligas: error — %s", exc, exc_info=True)
        text = "Error al obtener ligas. Usa /estado para diagnosticar el sistema."

    await update.message.reply_text(text, parse_mode="HTML")


def _format_ligas(leagues: list[dict]) -> str:
    if not leagues:
        return (
            "No hay ligas registradas en la base de datos.\n\n"
            "Aplica la migración y ejecuta:\n"
            "  <code>python scripts/sync_reference.py</code>\n"
            "  Luego corre el seed: <code>sql/seed_tracked_competitions.sql</code>"
        )

    # Group by tier for display
    tier_order = ["tier_1_daily", "tier_2_matchday", "tier_3_light"]
    by_tier: dict[str, list[dict]] = {t: [] for t in tier_order}
    for lg in leagues:
        t = lg.get("sync_tier", "tier_1_daily")
        by_tier.setdefault(t, []).append(lg)

    lines: list[str] = [f"<b>Ligas activas ({len(leagues)})</b>\n"]

    for tier in tier_order:
        group = by_tier.get(tier, [])
        if not group:
            continue
        tier_lbl = _TIER_LABEL.get(tier, tier)
        lines.append(f"<b>── {tier_lbl} ──</b>")
        for lg in group:
            pid = lg.get("provider_league_id")
            name = lg.get("name", "?")
            country = lg.get("country", "")
            season = lg.get("season", "?")
            coverage = lg.get("coverage") or {}

            cov_parts = []
            if coverage.get("standings"):
                cov_parts.append("standings")
            if coverage.get("injuries"):
                cov_parts.append("injuries")
            if coverage.get("predictions"):
                cov_parts.append("predictions")
            if coverage.get("odds"):
                cov_parts.append("odds")
            cov_str = ", ".join(cov_parts) if cov_parts else "sin coverage"

            lines.append(
                f"  <b>{name}</b> ({country})\n"
                f"  ID: <code>{pid}</code> · Season: <b>{season}</b>\n"
                f"  Coverage: {cov_str}"
            )
        lines.append("")

    lines.append(
        "<i>T1 = sync diario · T2 = solo en jornadas · T3 = fixtures+odds básico</i>"
    )
    return "\n".join(lines)
