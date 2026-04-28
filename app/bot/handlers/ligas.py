"""Handler for /ligas — shows active leagues grouped by tier, with pagination."""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.middleware.db_guard import ensure_db_ready
from app.bot.utils import send_html
from app.data.repositories.fixture_repo import get_active_leagues

logger = logging.getLogger(__name__)

_TIER_LABEL = {
    "tier_1_daily":   "T1 — Diario",
    "tier_2_matchday": "T2 — Jornada",
    "tier_3_light":   "T3 — Ligero",
}
_TIER_KEYS = ["tier_1_daily", "tier_2_matchday", "tier_3_light"]


@require_auth
async def ligas_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ligas [1|2|3] — List active leagues grouped by tier.

    /ligas     → overview: count per tier + navigation hint
    /ligas 1   → tier_1_daily leagues
    /ligas 2   → tier_2_matchday leagues
    /ligas 3   → tier_3_light leagues
    """
    logger.info("/ligas solicitado por user_id=%s", update.effective_user.id)

    if not await ensure_db_ready(update, context):
        return

    page = 0
    if context.args:
        try:
            page = int(context.args[0])
        except ValueError:
            page = 0

    try:
        leagues = get_active_leagues()
    except Exception as exc:
        logger.error("/ligas: error — %s", exc, exc_info=True)
        await update.message.reply_text(
            "Error al obtener ligas. Usa /estado para diagnosticar el sistema."
        )
        return

    if page == 0:
        text = _format_overview(leagues)
    elif 1 <= page <= 3:
        text = _format_tier_page(leagues, _TIER_KEYS[page - 1], page)
    else:
        text = "Página inválida. Usa /ligas, /ligas 1, /ligas 2, o /ligas 3."

    await send_html(update.message, text)


def _format_overview(leagues: list[dict]) -> str:
    if not leagues:
        return (
            "No hay ligas registradas en la base de datos.\n\n"
            "Aplica la migración y ejecuta:\n"
            "  <code>python scripts/sync_reference.py</code>\n"
            "  Luego: <code>sql/seed_tracked_competitions.sql</code>"
        )

    by_tier: dict[str, list] = {t: [] for t in _TIER_KEYS}
    other = 0
    for lg in leagues:
        t = lg.get("sync_tier") or "tier_1_daily"
        if t in by_tier:
            by_tier[t].append(lg)
        else:
            other += 1

    lines = [f"<b>Ligas activas: {len(leagues)}</b>\n"]
    for i, tier in enumerate(_TIER_KEYS, 1):
        lbl = _TIER_LABEL[tier]
        count = len(by_tier[tier])
        lines.append(f"  Página {i} — {lbl}: <b>{count}</b> ligas")
    if other:
        lines.append(f"  (otras / sin tier): {other}")

    lines.append("")
    lines.append("Ver detalle: /ligas 1  |  /ligas 2  |  /ligas 3")
    lines.append("")
    lines.append(
        "<i>T1 = sync diario · T2 = sólo en jornadas · T3 = fixtures+odds básico</i>"
    )
    return "\n".join(lines)


def _format_tier_page(leagues: list[dict], tier: str, page: int) -> str:
    group = [lg for lg in leagues if (lg.get("sync_tier") or "") == tier]
    lbl = _TIER_LABEL.get(tier, tier)

    if not group:
        return (
            f"No hay ligas en {lbl}.\n\n"
            "Ejecuta <code>python scripts/sync_tracked_competitions_from_env.py</code> "
            "para cargar ligas desde .env."
        )

    group.sort(key=lambda x: x.get("provider_league_id") or 0)

    lines = [f"<b>Página {page} — {lbl}</b> ({len(group)} ligas)\n"]
    for lg in group:
        pid = lg.get("provider_league_id", "?")
        name = lg.get("name", "?")
        country = lg.get("country", "")
        season = lg.get("season", "?")
        country_str = f" ({country})" if country else ""
        lines.append(f"[{pid}] {name}{country_str} · S:{season}")

    other_pages = [str(i) for i in range(1, 4) if i != page]
    lines.append("")
    lines.append(
        "<i>Otras páginas: "
        + " · ".join(f"/ligas {p}" for p in other_pages)
        + " · /ligas (resumen)</i>"
    )
    return "\n".join(lines)
