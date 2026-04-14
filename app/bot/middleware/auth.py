"""Centralized authentication decorator for Telegram handlers."""

import logging
import functools
from typing import Callable, Awaitable, Any

from telegram import Update
from telegram.ext import ContextTypes

from app.core.config import settings

logger = logging.getLogger(__name__)

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


def require_auth(handler: Handler) -> Handler:
    """Decorator that rejects any Telegram user not in the allow-list.

    Logs unauthorized attempts and sends a generic rejection message without
    revealing information about the bot's existence or configuration.

    Usage::

        @require_auth
        async def my_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            ...
    """

    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user or user.id != settings.telegram_allowed_user_id:
            uid = user.id if user else "unknown"
            logger.warning(
                "Acceso no autorizado: user_id=%s intenta %s",
                uid,
                handler.__name__,
            )
            if update.message:
                await update.message.reply_text("Acceso no autorizado.")
            return
        await handler(update, context)

    return wrapper  # type: ignore[return-value]
