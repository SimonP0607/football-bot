"""DB readiness guard for Telegram handlers.

Checks whether the database schema is initialized before a handler runs.
Self-heals: if the startup check failed but migrations have since been applied,
the next command re-probes live and updates the cached status — no bot restart
required.

Usage in a handler::

    from app.bot.middleware.db_guard import ensure_db_ready

    @require_auth
    async def my_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await ensure_db_ready(update, context):
            return   # user-friendly message already sent
        ...
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.data.db_health import SchemaStatus, check_schema

logger = logging.getLogger(__name__)

_MSG_CONN_FAIL = (
    "⚠️ <b>No se puede conectar a la base de datos.</b>\n\n"
    "Verifica <code>SUPABASE_URL</code> y <code>SUPABASE_KEY</code> en tu archivo <code>.env</code>.\n\n"
    "Luego reinicia el bot."
)

_MSG_NOT_INITIALIZED = (
    "⚠️ <b>La base de datos no está inicializada.</b>\n\n"
    "Las migraciones SQL aún no se han aplicado.\n\n"
    "<b>Para solucionarlo:</b>\n"
    "1. Abre <b>Supabase → SQL Editor</b>\n"
    "2. Ejecuta <code>sql/migrations/001_init.sql</code>\n"
    "3. Ejecuta <code>sql/migrations/002_constraints_and_indexes.sql</code>\n"
    "4. Reinicia el bot\n"
    "5. Luego sincroniza: <code>python scripts/sync_today.py</code>\n\n"
    "Guía completa: <code>python scripts/bootstrap_database.py</code>"
)


async def ensure_db_ready(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    """Return True if the DB schema is ready; send a message and return False if not.

    Caching strategy:
    - If ``bot_data["schema_status"]`` is present and OK → return True (no extra query).
    - Otherwise re-probe live so the bot self-heals after migrations are applied.
    """
    schema: SchemaStatus | None = context.bot_data.get("schema_status")

    if schema is not None and schema.db_ready:
        return True

    # Re-probe: migrations may have been applied since startup (or status missing)
    schema = check_schema()
    context.bot_data["schema_status"] = schema

    if schema.db_ready:
        logger.info("DB guard: re-probe OK — schema ahora disponible, sin necesidad de reiniciar")
        return True

    # Build message
    if not schema.connection_ok:
        msg = _MSG_CONN_FAIL
    else:
        missing_html = ", ".join(f"<code>{t}</code>" for t in schema.missing_tables)
        msg = _MSG_NOT_INITIALIZED + f"\n\nTablas faltantes: {missing_html}"

    logger.warning(
        "DB guard: schema no listo (%s) — bloqueando handler para user_id=%s",
        schema.label,
        update.effective_user.id if update.effective_user else "?",
    )
    if update.message:
        await update.message.reply_text(msg, parse_mode="HTML")
    return False
