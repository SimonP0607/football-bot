import logging
from telegram import BotCommand, Update
from telegram.ext import (
    Application, ApplicationBuilder, CallbackQueryHandler,
    CommandHandler, ContextTypes, MessageHandler, filters,
)

from app.core.logger import setup_logger
from app.core.config import settings
from app.bot.handlers.start import start_handler
from app.bot.handlers.hoy import hoy_handler
from app.bot.handlers.top import top_handler
from app.bot.handlers.estado import estado_handler
from app.bot.handlers.id import id_handler
from app.bot.handlers.debug_config import debug_config_handler
from app.bot.handlers.partido import partido_handler
from app.bot.handlers.ligas import ligas_handler
from app.bot.handlers.valor import valor_handler
from app.bot.handlers.resultados import resultados_handler
from app.bot.handlers.rendimiento import rendimiento_handler
from app.bot.handlers.equipo import equipo_handler, jugador_handler
from app.bot.handlers.live import live_handler
from app.bot.handlers.seguimiento import seguimiento_handler
from app.bot.handlers.parlay import parlay_handler
from app.bot.handlers.ayuda import ayuda_handler
from app.bot.handlers.menu import menu_handler
from app.bot.handlers.callbacks import callback_handler
from app.bot.handlers.conversation import conversation_handler
from app.bot.handlers.alertas import alertas_handler
from app.bot.handlers.scheduler import scheduler_handler

logger = logging.getLogger(__name__)

# Commands visible in the Telegram UI (hamburger menu / "/" autocomplete)
_BOT_COMMANDS = [
    BotCommand("start", "Información y lista de comandos"),
    BotCommand("hoy", "Picks publicables del día"),
    BotCommand("top", "Top picks por confianza"),
    BotCommand("partido", "Análisis de un partido concreto"),
    BotCommand("ligas", "Ligas activas y su cobertura"),
    BotCommand("valor", "Métricas del value engine (último sync)"),
    BotCommand("resultados", "Últimos picks resueltos (win/loss/void)"),
    BotCommand("rendimiento", "ROI, hit rate y yield del sistema"),
    BotCommand("estado", "Estado del sistema (Telegram, DB, API, datos)"),
    BotCommand("equipo", "Buscar equipo en el catálogo (nombre o ID)"),
    BotCommand("jugador", "Buscar jugador en el catálogo"),
    BotCommand("live", "Picks en juego ahora (estado live)"),
    BotCommand("seguimiento", "Seguimiento de un partido concreto"),
    BotCommand("parlay", "Parlays recomendados del día (combinadas)"),
    BotCommand("menu", "Menú interactivo con botones"),
    BotCommand("ayuda", "Ayuda completa y guía de uso"),
    BotCommand("alertas", "Alertas proactivas y estado del scheduler"),
    BotCommand("scheduler", "Control del scheduler (admin)"),
    BotCommand("id", "Ver tu Telegram user ID (setup inicial)"),
]


async def _post_init(application: Application) -> None:
    """Run once after the bot connects — registers commands, checks DB schema."""

    # ── Register commands ──────────────────────────────────────────────────────
    await application.bot.set_my_commands(_BOT_COMMANDS)
    logger.info("Comandos del bot registrados en Telegram (%d comandos)", len(_BOT_COMMANDS))

    # ── Bootstrap mode warning ─────────────────────────────────────────────────
    if settings.is_bootstrap_mode:
        logger.warning("═" * 65)
        logger.warning("  MODO BOOTSTRAP — bot sin propietario configurado")
        logger.warning("  Pasos para activarlo:")
        logger.warning("    1. Abre Telegram y envía /id al bot")
        logger.warning("    2. Copia el número que te devuelve")
        logger.warning("    3. Pégalo en .env: TELEGRAM_ALLOWED_USER_ID=<número>")
        logger.warning("    4. Reinicia el bot")
        logger.warning("  Alternativa: python scripts/get_telegram_user_id.py")
        logger.warning("═" * 65)
    else:
        logger.info("Propietario configurado: user_id=%s", settings.telegram_allowed_user_id)

    # ── Scheduler setup ────────────────────────────────────────────────────────
    from app.services.scheduler_service import setup_scheduler
    setup_scheduler(application)

    # ── Database schema check ──────────────────────────────────────────────────
    from app.data.db_health import check_schema
    schema = check_schema()
    application.bot_data["schema_status"] = schema

    if not schema.connection_ok:
        logger.error(
            "Supabase CONN_FAIL al arrancar — %s\n"
            "  Verifica SUPABASE_URL y SUPABASE_KEY en .env",
            schema.error,
        )
    elif not schema.tables_ok:
        logger.error("═" * 65)
        logger.error("  DB_NOT_INITIALIZED — las migraciones no están aplicadas")
        logger.error("  Tablas faltantes: %s", ", ".join(schema.missing_tables))
        logger.error("  Solución: Supabase → SQL Editor, ejecutar en orden:")
        logger.error("    1. sql/migrations/010_production_schema.sql")
        logger.error("    2. sql/migrations/011_sync_tier.sql")
        logger.error("    3. sql/migrations/012_settlement.sql")
        logger.error("    4. sql/migrations/013_retention_cleanup.sql")
        logger.error("    5. sql/seed_tracked_competitions.sql")
        logger.error("    6. python scripts/sync_reference.py")
        logger.error("    7. python scripts/sync_today.py")
        logger.error("═" * 65)
    else:
        logger.info("Supabase: conexión OK, schema aplicado — todas las tablas presentes")


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log all Telegram dispatcher errors so they never disappear silently."""
    logger.error("Error en el dispatcher de Telegram", exc_info=context.error)
    if isinstance(update, Update) and update.message:
        await update.message.reply_text(
            "Ocurrió un error interno. Usa /estado para diagnosticar el sistema."
        )


def build_app() -> Application:
    setup_logger()
    logger.info(
        "Iniciando bot (env=%s, token=%s)",
        settings.app_env,
        settings.masked_token,
    )

    application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("hoy", hoy_handler))
    application.add_handler(CommandHandler("top", top_handler))
    application.add_handler(CommandHandler("partido", partido_handler))
    application.add_handler(CommandHandler("ligas", ligas_handler))
    application.add_handler(CommandHandler("valor", valor_handler))
    application.add_handler(CommandHandler("resultados", resultados_handler))
    application.add_handler(CommandHandler("rendimiento", rendimiento_handler))
    application.add_handler(CommandHandler("equipo", equipo_handler))
    application.add_handler(CommandHandler("jugador", jugador_handler))
    application.add_handler(CommandHandler("live", live_handler))
    application.add_handler(CommandHandler("seguimiento", seguimiento_handler))
    application.add_handler(CommandHandler("parlay", parlay_handler))
    application.add_handler(CommandHandler("ayuda", ayuda_handler))
    application.add_handler(CommandHandler("menu", menu_handler))
    application.add_handler(CommandHandler("alertas", alertas_handler))
    application.add_handler(CommandHandler("scheduler", scheduler_handler))
    application.add_handler(CommandHandler("estado", estado_handler))
    application.add_handler(CommandHandler("id", id_handler))

    # /debug_config only registered in local/dev mode
    if settings.is_local:
        application.add_handler(CommandHandler("debug_config", debug_config_handler))
        logger.debug("/debug_config registrado (solo disponible en modo local)")

    # CallbackQueryHandler for inline keyboard buttons
    application.add_handler(CallbackQueryHandler(callback_handler))

    # MessageHandler for free-text (conversational AI router)
    # Must be added LAST so command handlers take priority
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, conversation_handler))

    application.add_error_handler(_error_handler)

    return application
