"""Handler for /hoy — today's publishable picks."""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.middleware.db_guard import ensure_db_ready
from app.bot.formatters.pick_formatter import format_picks
from app.bot.utils import send_html
from app.data.repositories.fixture_repo import get_teams_by_ids, get_leagues_by_ids
from app.services.prediction_service import prediction_service

logger = logging.getLogger(__name__)


@require_auth
async def hoy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/hoy — Send today's publishable picks."""
    logger.info("/hoy solicitado por user_id=%s", update.effective_user.id)

    if not await ensure_db_ready(update, context):
        return  # user-friendly message already sent by the guard

    try:
        predictions, fixtures = prediction_service.get_today_picks()

        # Batch-resolve team and league names (2 queries regardless of fixture count)
        team_ids = {fix["home_team_id"] for fix in fixtures} | {fix["away_team_id"] for fix in fixtures}
        league_ids = {fix["league_id"] for fix in fixtures}

        team_names = get_teams_by_ids(team_ids)
        league_names = get_leagues_by_ids(league_ids)

        text = format_picks(predictions, fixtures, team_names, league_names)

    except Exception as exc:
        logger.error("/hoy: error al consultar la base de datos — %s", exc, exc_info=True)
        # Invalidate cached status so the next command re-probes
        context.bot_data.pop("schema_status", None)
        text = (
            "Error al consultar la base de datos.\n\n"
            "Usa /estado para ver el estado del sistema y revisa los logs."
        )

    await send_html(update.message, text)
