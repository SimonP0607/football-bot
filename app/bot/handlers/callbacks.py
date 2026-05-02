"""Phase 9: Callback query handler for inline keyboard buttons.

Handles callback_data patterns:
  menu:<target>       — route to a main handler
  parlay:<legs|risk>  — parlay sub-commands
  fixture:<action>:<id> — fixture-specific actions
  live:<action>       — live monitoring actions
  leagues:<page>      — leagues pagination
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.core.config import settings

logger = logging.getLogger(__name__)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch inline keyboard callbacks to the appropriate handler."""
    query = update.callback_query
    if not query:
        return

    # Auth check
    user = update.effective_user
    if not user or user.id != settings.telegram_allowed_user_id:
        if settings.is_bootstrap_mode:
            await query.answer("Bot en modo bootstrap. Configura TELEGRAM_ALLOWED_USER_ID.")
        else:
            await query.answer("Acceso no autorizado.")
        return

    await query.answer()  # dismiss the loading spinner

    data = query.data or ""
    logger.info("callback: user_id=%s data=%r", user.id, data)

    try:
        await _dispatch(query, update, context, data)
    except Exception as exc:
        logger.error("callback_handler: error for data=%r — %s", data, exc, exc_info=True)
        try:
            await query.message.reply_html(
                "Error al procesar la acción. Usa /estado para diagnosticar."
            )
        except Exception:
            pass


async def _dispatch(query, update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    """Route callback_data to the appropriate action."""
    from app.bot.handlers.top import top_handler
    from app.bot.handlers.hoy import hoy_handler
    from app.bot.handlers.parlay import parlay_handler
    from app.bot.handlers.estado import estado_handler
    from app.bot.handlers.rendimiento import rendimiento_handler
    from app.bot.handlers.resultados import resultados_handler
    from app.bot.handlers.valor import valor_handler
    from app.bot.handlers.live import live_handler
    from app.bot.handlers.ligas import ligas_handler
    from app.bot.handlers.ayuda import ayuda_handler
    from app.bot.handlers.menu import menu_handler

    msg = query.message

    # ── menu: routes ──────────────────────────────────────────────────────────
    if data == "menu:top":
        context.args = []
        await top_handler(update, context)

    elif data == "menu:hoy":
        context.args = []
        await hoy_handler(update, context)

    elif data == "menu:parlay":
        context.args = []
        await parlay_handler(update, context)

    elif data == "menu:estado":
        context.args = []
        await estado_handler(update, context)

    elif data == "menu:rendimiento":
        context.args = []
        await rendimiento_handler(update, context)

    elif data == "menu:resultados":
        context.args = []
        await resultados_handler(update, context)

    elif data == "menu:valor":
        context.args = []
        await valor_handler(update, context)

    elif data == "menu:live":
        context.args = []
        await live_handler(update, context)

    elif data == "menu:ligas":
        context.args = []
        await ligas_handler(update, context)

    elif data == "menu:ayuda":
        context.args = []
        await ayuda_handler(update, context)

    elif data in ("menu:back", "menu:menu"):
        context.args = []
        await menu_handler(update, context)

    # ── parlay: routes ────────────────────────────────────────────────────────
    elif data.startswith("parlay:"):
        suffix = data[len("parlay:"):]
        if suffix == "risk":
            context.args = ["riesgo"]
        elif suffix in ("2", "3", "4"):
            context.args = [suffix]
        else:
            context.args = []
        await parlay_handler(update, context)

    # ── fixture: routes ───────────────────────────────────────────────────────
    elif data.startswith("fixture:"):
        parts = data.split(":", 2)
        action = parts[1] if len(parts) > 1 else ""
        fid    = parts[2] if len(parts) > 2 else ""

        if action == "follow":
            from app.bot.handlers.seguimiento import seguimiento_handler
            context.args = [fid]
            await seguimiento_handler(update, context)
        elif action == "detail":
            from app.bot.handlers.partido import partido_handler
            context.args = [fid]
            await partido_handler(update, context)
        else:
            await msg.reply_html(f"Acción de fixture desconocida: <code>{data}</code>")

    # ── live: routes ──────────────────────────────────────────────────────────
    elif data.startswith("live:"):
        parts = data.split(":", 2)
        action = parts[1] if len(parts) > 1 else ""
        fid    = parts[2] if len(parts) > 2 else None

        if action == "refresh":
            context.args = []
            await live_handler(update, context)
        elif action == "detail" and fid:
            from app.bot.handlers.seguimiento import seguimiento_handler
            context.args = [fid]
            await seguimiento_handler(update, context)
        else:
            context.args = []
            await live_handler(update, context)

    # ── leagues: pagination ────────────────────────────────────────────────────
    elif data.startswith("leagues:"):
        suffix = data[len("leagues:"):]
        context.args = [suffix] if suffix.isdigit() else []
        await ligas_handler(update, context)

    else:
        logger.warning("callback: unknown data=%r", data)
        await msg.reply_html(f"Acción no reconocida: <code>{data}</code>")
