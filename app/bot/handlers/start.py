import logging
from telegram import Update
from telegram.ext import ContextTypes
from app.core.config import settings

logger = logging.getLogger(__name__)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or user.id != settings.telegram_allowed_user_id:
        uid = user.id if user else "unknown"
        logger.warning("Acceso no autorizado desde user_id=%s", uid)
        await update.message.reply_text("Acceso no autorizado.")
        return

    logger.info("Comando /start desde user_id=%s", user.id)
    await update.message.reply_text(
        "Bot de pronósticos listo.\n\n"
        "Comandos disponibles:\n"
        "/hoy — picks publicables del día\n"
        "/top — mejores picks por confianza\n"
        "/estado — resumen de sincronización\n"
        "/start — este mensaje"
    )
