"""Handler for the /id command — shows the user their Telegram user ID.

This command intentionally has NO auth check. Its only purpose is to help
the bot owner find their numeric Telegram user ID so they can fill in
TELEGRAM_ALLOWED_USER_ID in .env during the initial setup.

It reveals no sensitive information: every Telegram user already knows
their own ID, and the command is only accessible to whoever has the bot token.
Once the bot is configured, this command can be left in place safely.
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def id_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/id — Display the caller's Telegram user ID (no auth required)."""
    user = update.effective_user
    if not user:
        return

    logger.info("/id solicitado por user_id=%s username=%s", user.id, user.username)
    await update.message.reply_text(
        f"Tu Telegram user ID es:\n\n"
        f"<code>{user.id}</code>\n\n"
        f"Cópialo y pégalo como valor de <code>TELEGRAM_ALLOWED_USER_ID</code> en tu archivo <code>.env</code>.",
        parse_mode="HTML",
    )
