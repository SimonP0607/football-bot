"""Handler for the /debug_config command — shows masked configuration state.

Available in local/dev mode only (APP_ENV != production).
No auth check: if you have the token, you can see a masked config summary.
Secrets are always masked — no raw keys or tokens are ever shown.
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.core.config import settings

logger = logging.getLogger(__name__)


async def debug_config_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/debug_config — Show masked configuration status (local mode only)."""
    if not update.message:
        return

    if not settings.is_local:
        # Silently ignore in production — do not reveal that this command exists
        return

    owner_line = (
        f"<code>{settings.telegram_allowed_user_id}</code>"
        if not settings.is_bootstrap_mode
        else "<b>NO CONFIGURADO</b> (modo bootstrap activo)"
    )

    league_ids = ", ".join(str(i) for i in settings.league_ids_list) or "(no configurado)"
    season = str(settings.default_season) if settings.default_season else "(no configurado)"

    env_file_status = "encontrado" if _env_file_exists() else "no encontrado"

    text = (
        "<b>Estado de configuración</b>\n"
        f"{'─' * 32}\n"
        f"<b>Entorno:</b>           {settings.app_env}\n"
        f"<b>Log level:</b>         {settings.log_level}\n"
        f"\n"
        f"<b>Bot token:</b>         <code>{settings.masked_token}</code>\n"
        f"<b>Owner ID:</b>          {owner_line}\n"
        f"\n"
        f"<b>Supabase URL:</b>      {settings.supabase_url or '(no configurado)'}\n"
        f"<b>Supabase key:</b>      <code>{settings.masked_supabase_key}</code>\n"
        f"\n"
        f"<b>API-Football URL:</b>  {settings.api_football_base_url}\n"
        f"<b>API-Football key:</b>  <code>{settings.masked_api_key}</code>\n"
        f"\n"
        f"<b>Zona horaria:</b>      {settings.default_timezone}\n"
        f"<b>Mercados:</b>          {settings.default_markets}\n"
        f"<b>Ligas:</b>             {league_ids}\n"
        f"<b>Temporada:</b>         {season}\n"
        f"<b>Max picks/día:</b>     {settings.max_daily_picks}\n"
        f"<b>Min edge:</b>          {settings.min_edge}\n"
        f"<b>Min confianza:</b>     {settings.min_confidence}\n"
        f"\n"
        f"<b>Archivo .env:</b>      {env_file_status}\n"
    )

    if settings.is_bootstrap_mode:
        text += (
            f"\n"
            f"⚠️ <b>Modo bootstrap</b>: configura <code>TELEGRAM_ALLOWED_USER_ID</code> en .env\n"
            f"Envía /id para ver tu user ID numérico."
        )

    logger.info("/debug_config ejecutado por user_id=%s", update.effective_user and update.effective_user.id)
    await update.message.reply_text(text, parse_mode="HTML")


def _env_file_exists() -> bool:
    from pathlib import Path
    env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
    return env_path.is_file()
