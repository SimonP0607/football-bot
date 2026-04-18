import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth

logger = logging.getLogger(__name__)


@require_auth
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Comando /start desde user_id=%s", update.effective_user.id)
    await update.message.reply_text(
        "Bot de pronósticos listo.\n\n"
        "Comandos disponibles:\n"
        "/hoy — picks publicables del día\n"
        "/top — mejores picks por confianza\n"
        "/partido &lt;id o equipo&gt; — análisis de un partido\n"
        "/ligas — ligas activas y cobertura\n"
        "/estado — resumen de sincronización y cuota API\n"
        "/id — muestra tu Telegram user ID\n"
        "/start — este mensaje",
        parse_mode="HTML",
    )
