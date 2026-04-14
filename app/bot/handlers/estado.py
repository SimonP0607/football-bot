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
    """/estado — Show full system health: Telegram, Supabase, schema, and data counts."""
    logger.info("/estado solicitado por user_id=%s", update.effective_user.id)

    # Always re-probe schema so the report reflects the actual current state,
    # not the potentially stale value stored at startup.
    schema = check_schema()
    context.bot_data["schema_status"] = schema

    counts: dict | None = None
    if schema.db_ready:
        try:
            from app.services.prediction_service import prediction_service
            counts = prediction_service.get_estado()
        except Exception as exc:
            logger.error("/estado: error al obtener conteos de la base de datos — %s", exc)
            # counts stays None → format_estado shows the error row

    text = format_estado(schema, counts)
    await update.message.reply_text(text, parse_mode="HTML")
