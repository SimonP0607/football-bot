"""Handler for /estado — full system health and data status report.

Always runs, even when the DB is not initialized. This is intentional:
/estado is the diagnostic command the operator uses to understand what is
wrong and what to do next. It must never crash.
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.formatters.pick_formatter import format_estado
from app.data.db_health import check_schema

logger = logging.getLogger(__name__)


@require_auth
async def estado_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/estado — Show full system health: Telegram, Supabase, API-Football, and data counts."""
    logger.info("/estado solicitado por user_id=%s", update.effective_user.id)

    # Always re-probe schema so the report reflects the actual current state.
    schema = check_schema()
    context.bot_data["schema_status"] = schema

    counts: dict | None = None
    last_sync: dict | None = None
    rate_state: dict | None = None

    if schema.db_ready:
        try:
            from app.services.prediction_service import prediction_service
            counts = prediction_service.get_estado()
        except Exception as exc:
            logger.error("/estado: error al obtener conteos — %s", exc)

        try:
            from app.data.repositories.sync_runs_repo import get_last_sync_run
            last_sync = get_last_sync_run()
        except Exception as exc:
            logger.debug("/estado: no se pudo obtener last_sync_run — %s", exc)

    # Always try to read in-memory rate-limit state (updated on any API call)
    try:
        from app.data.api_football.client import get_rate_limit_state
        rate_state = get_rate_limit_state()
    except Exception as exc:
        logger.debug("/estado: no se pudo leer rate_state — %s", exc)

    text = format_estado(schema, counts, rate_state=rate_state, last_sync=last_sync)
    await update.message.reply_text(text, parse_mode="HTML")
