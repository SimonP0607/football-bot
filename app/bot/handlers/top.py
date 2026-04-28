"""Handler for /top — top picks ranked by quality score."""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.middleware.db_guard import ensure_db_ready
from app.bot.formatters.pick_formatter import format_top_picks
from app.bot.utils import send_html
from app.core.config import settings
from app.data.repositories.fixture_repo import get_teams_by_ids, get_leagues_by_ids
from app.services.prediction_service import prediction_service

logger = logging.getLogger(__name__)

# VE status constants — must match value_engine_live_adapter.STATUS_* values
_VE_SELECTED          = "value_selected"
_VE_LOW_QUALITY       = "value_rejected_low_quality"
_VE_LOW_EDGE          = "value_rejected_low_edge"
_VE_MISSING_HISTORY   = "value_rejected_missing_history"
_VE_MISSING_CALIB     = "value_rejected_missing_calibrator"
_VE_MISSING_ODDS      = "value_rejected_missing_odds"


@require_auth
async def top_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/top — Send the top picks ranked by composite quality score."""
    logger.info("/top solicitado por user_id=%s", update.effective_user.id)

    if not await ensure_db_ready(update, context):
        return

    try:
        predictions, fixtures = prediction_service.get_top_picks(
            limit=settings.max_daily_picks
        )

        team_ids = {fix["home_team_id"] for fix in fixtures} | {fix["away_team_id"] for fix in fixtures}
        league_ids = {fix["league_id"] for fix in fixtures}

        team_names = get_teams_by_ids(team_ids)
        league_names = get_leagues_by_ids(league_ids)

        # When no official picks and VE is active, surface a diagnostic breakdown
        # so the user understands why nothing was published.
        ve_summary: dict | None = None
        if not predictions and settings.value_engine_enabled and settings.value_engine_mode in ("shadow", "assist"):
            ve_summary = _build_ve_summary(fixtures, settings.value_engine_mode)

        text = format_top_picks(predictions, fixtures, team_names, league_names, ve_summary=ve_summary)

    except Exception as exc:
        logger.error("/top: error al consultar la base de datos — %s", exc, exc_info=True)
        context.bot_data.pop("schema_status", None)
        text = (
            "Error al consultar la base de datos.\n\n"
            "Usa /estado para ver el estado del sistema y revisa los logs."
        )

    await send_html(update.message, text)


def _build_ve_summary(fixtures: list[dict], mode: str) -> dict | None:
    """Count value engine statuses from today's pick_candidates.

    Returns a dict with counts per VE stage, or None if no VE data exists.
    """
    try:
        from app.data.repositories.prediction_repo import get_candidates_for_fixtures
        fixture_ids = [f["id"] for f in fixtures]
        if not fixture_ids:
            return None
        candidates = get_candidates_for_fixtures(fixture_ids)

        evaluated = 0
        selected = 0
        pickfilter_rejected = 0   # VE-selected but not publishable (model rejected them)
        low_quality = 0
        low_edge = 0
        missing = 0               # missing history or calibrator
        observados: list[dict] = []

        for c in candidates:
            ve_status = c.get("value_engine_status")
            if not ve_status or ve_status == "value_skipped_engine_off":
                pass  # still check for observado
            else:
                evaluated += 1
                if ve_status == _VE_SELECTED:
                    selected += 1
                    if not c.get("is_publishable"):
                        pickfilter_rejected += 1
                elif ve_status == _VE_LOW_QUALITY:
                    low_quality += 1
                elif ve_status == _VE_LOW_EDGE:
                    low_edge += 1
                elif ve_status in (_VE_MISSING_HISTORY, _VE_MISSING_CALIB, _VE_MISSING_ODDS):
                    missing += 1
                # _VE_ERROR counts are visible in /valor

            # Collect observados: positive edge, not publishable, not already a pick
            edge = c.get("edge") or 0.0
            if not c.get("is_publishable") and edge >= 0:
                observados.append(c)

        if evaluated == 0:
            return None

        observados.sort(key=lambda x: x.get("edge") or 0.0, reverse=True)
        return {
            "mode": mode,
            "evaluated": evaluated,
            "selected": selected,
            "pickfilter_rejected": pickfilter_rejected,
            "low_quality": low_quality,
            "low_edge": low_edge,
            "missing": missing,
            "_observados": observados[:3],
        }
    except Exception as exc:
        logger.debug("/top: no se pudo construir ve_summary: %s", exc)
        return None
