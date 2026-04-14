"""Centralized authentication decorator for Telegram handlers."""

import logging
import functools
from typing import Callable, Awaitable

from telegram import Update
from telegram.ext import ContextTypes

from app.core.config import settings

logger = logging.getLogger(__name__)

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]

_BOOTSTRAP_MESSAGE = (
    "El bot está en <b>modo bootstrap</b> — aún no tienes un propietario configurado.\n\n"
    "Para activar el bot:\n"
    "1. Envía /id para ver tu Telegram user ID\n"
    "2. Cópialo en tu archivo <code>.env</code> como <code>TELEGRAM_ALLOWED_USER_ID</code>\n"
    "3. Reinicia el bot\n\n"
    "Si ya lo configuraste, asegúrate de que el bot se haya reiniciado."
)


def require_auth(handler: Handler) -> Handler:
    """Decorator that only allows the configured owner to run protected commands.

    Bootstrap mode (TELEGRAM_ALLOWED_USER_ID=0):
      - Returns a friendly setup message instead of a generic rejection.
      - Logs the attempt so the owner can see their user_id in the output.

    Production mode:
      - Rejects any user not matching TELEGRAM_ALLOWED_USER_ID with a generic
        message that reveals nothing about the bot's configuration.

    Usage::

        @require_auth
        async def my_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            ...
    """

    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        uid = user.id if user else "unknown"

        if not user or user.id != settings.telegram_allowed_user_id:
            if settings.is_bootstrap_mode:
                # Help the owner configure their ID
                logger.info(
                    "Bootstrap: user_id=%s (@%s) intentó /%s — bot sin propietario configurado",
                    uid,
                    getattr(user, "username", "?"),
                    handler.__name__,
                )
                if update.message:
                    await update.message.reply_text(
                        _BOOTSTRAP_MESSAGE, parse_mode="HTML"
                    )
            else:
                # Unknown user in a fully configured bot — log but don't reveal details
                logger.warning(
                    "Acceso no autorizado: user_id=%s intenta /%s",
                    uid,
                    handler.__name__,
                )
                if update.message:
                    await update.message.reply_text("Acceso no autorizado.")
            return

        await handler(update, context)

    return wrapper  # type: ignore[return-value]
