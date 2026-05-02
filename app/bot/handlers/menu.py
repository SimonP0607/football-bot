"""Handler for /menu — interactive main menu with inline buttons."""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.ui.keyboard import main_menu_keyboard

logger = logging.getLogger(__name__)

_MENU_TEXT = (
    "<b>Menú principal</b>\n\n"
    "Selecciona una opción o escribe en lenguaje natural.\n"
    "Ejemplo: <i>\"dame una combinada conservadora\"</i>"
)


@require_auth
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/menu — Show interactive main menu."""
    logger.info("/menu solicitado por user_id=%s", update.effective_user.id)
    await update.message.reply_html(
        _MENU_TEXT,
        reply_markup=main_menu_keyboard(),
    )
