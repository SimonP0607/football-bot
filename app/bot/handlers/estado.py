import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.formatters.pick_formatter import format_estado
from app.services.prediction_service import prediction_service

logger = logging.getLogger(__name__)


@require_auth
async def estado_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/estado — Send a summary of today's sync and prediction status."""
    logger.info("/estado solicitado por user_id=%s", update.effective_user.id)
    estado = prediction_service.get_estado()
    text = format_estado(estado)
    await update.message.reply_text(text, parse_mode="HTML")
