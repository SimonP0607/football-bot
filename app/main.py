import logging
from telegram import BotCommand, Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes

from app.core.logger import setup_logger
from app.core.config import settings
from app.bot.handlers.start import start_handler
from app.bot.handlers.hoy import hoy_handler
from app.bot.handlers.top import top_handler
from app.bot.handlers.estado import estado_handler
from app.bot.handlers.id import id_handler

logger = logging.getLogger(__name__)

# Commands visible in the Telegram UI (hamburger menu / "/" autocomplete)
_BOT_COMMANDS = [
    BotCommand("start", "Información y lista de comandos"),
    BotCommand("hoy", "Picks publicables del día"),
    BotCommand("top", "Top picks por confianza"),
    BotCommand("estado", "Estado de sync y predicciones"),
    BotCommand("id", "Ver tu Telegram user ID (setup inicial)"),
]


async def _post_init(application: Application) -> None:
    """Run once after the bot connects — registers commands and checks DB."""
    # Register commands so they appear in the Telegram UI
    await application.bot.set_my_commands(_BOT_COMMANDS)
    logger.info("Comandos del bot registrados en Telegram (%d comandos)", len(_BOT_COMMANDS))

    # Non-fatal Supabase connectivity check
    try:
        from app.data.repositories.supabase_client import get_supabase
        client = get_supabase()
        client.table("bot_users").select("id").limit(1).execute()
        logger.info("Supabase: conexión verificada correctamente")
    except Exception as exc:
        logger.warning(
            "Supabase: no se pudo verificar la conexión al arrancar — %s. "
            "Verifica SUPABASE_URL y SUPABASE_KEY, y que las migraciones estén aplicadas.",
            exc,
        )


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log all Telegram dispatcher errors so they never disappear silently."""
    logger.error("Error en el dispatcher de Telegram", exc_info=context.error)
    if isinstance(update, Update) and update.message:
        await update.message.reply_text("Ocurrió un error interno. Revisa los logs.")


def build_app() -> Application:
    setup_logger()
    logger.info("Iniciando bot (env=%s)", settings.app_env)

    application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("hoy", hoy_handler))
    application.add_handler(CommandHandler("top", top_handler))
    application.add_handler(CommandHandler("estado", estado_handler))
    application.add_handler(CommandHandler("id", id_handler))

    application.add_error_handler(_error_handler)

    return application
