"""Handler for /resultados — recently settled picks with results."""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.middleware.db_guard import ensure_db_ready
from app.bot.utils import esc, send_html

logger = logging.getLogger(__name__)

_DAYS_DEFAULT = 14
_SHOW_MAX = 12

_MKT_LABEL = {
    "1X2":  "1X2",
    "OU25": "O/U 2.5",
    "BTTS": "BTTS",
}
_SEL_LABEL: dict[tuple[str, str], str] = {
    ("1X2",  "Home"):      "Local",
    ("1X2",  "Draw"):      "Empate",
    ("1X2",  "Away"):      "Visitante",
    ("OU25", "Over 2.5"):  "Más 2.5",
    ("OU25", "Under 2.5"): "Menos 2.5",
    ("BTTS", "Yes"):       "Sí anotan",
    ("BTTS", "No"):        "No anotan",
}


def _result_icon(status: str) -> str:
    return {"win": "✅", "loss": "❌", "void": "🔘"}.get(status, "❓")


def _mkt(market: str) -> str:
    return _MKT_LABEL.get(market, market)


def _sel(market: str, selection: str) -> str:
    return _SEL_LABEL.get((market, selection), selection)


@require_auth
async def resultados_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/resultados — Show recently settled picks with results and profit."""
    logger.info("/resultados solicitado por user_id=%s", update.effective_user.id)

    if not await ensure_db_ready(update, context):
        return

    try:
        text = _build_text()
    except Exception as exc:
        logger.error("/resultados: error — %s", exc, exc_info=True)
        text = "Error al obtener resultados. Usa /estado para diagnosticar."

    await send_html(update.message, text)


def _build_text() -> str:
    from app.data.repositories import settlement_repo
    from app.data.repositories.supabase_client import get_supabase

    rows = settlement_repo.get_settled(days=_DAYS_DEFAULT)

    lines: list[str] = [f"<b>Resultados — últimos {_DAYS_DEFAULT} días</b>", ""]

    if not rows:
        lines.append("Sin picks resueltos en los últimos 14 días.")
        lines.append("")
        lines.append("Si hay picks pendientes:")
        lines.append("<code>python scripts/settle_results.py --dry-run</code>")
        lines.append("<code>python scripts/settle_results.py</code>")
        return "\n".join(lines)

    # Resolve fixture info (team names, league)
    fixture_ids = list({r["fixture_id"] for r in rows})
    client = get_supabase()

    fix_rows = (
        client.table("fixtures")
        .select("id, home_team_id, away_team_id, league_id, kickoff_at, provider_fixture_id")
        .in_("id", fixture_ids)
        .execute()
    ).data or []
    fix_map = {f["id"]: f for f in fix_rows}

    team_ids = set()
    league_ids = set()
    for f in fix_rows:
        team_ids.add(f["home_team_id"])
        team_ids.add(f["away_team_id"])
        if f.get("league_id"):
            league_ids.add(f["league_id"])

    team_rows = (
        client.table("teams").select("id, name").in_("id", list(team_ids)).execute()
    ).data or []
    team_map = {t["id"]: t["name"] for t in team_rows}

    # competition_seasons → competition_id
    cs_rows = (
        client.table("competition_seasons")
        .select("id, competition_id")
        .in_("id", list(league_ids))
        .execute()
    ).data or []
    comp_ids = list({c["competition_id"] for c in cs_rows if c.get("competition_id")})
    comp_rows = (
        client.table("competitions").select("id, name").in_("id", comp_ids).execute()
    ).data or []
    comp_map = {c["id"]: c["name"] for c in comp_rows}
    cs_map = {c["id"]: comp_map.get(c["competition_id"], "?") for c in cs_rows}

    # Stats summary
    stats = settlement_repo.compute_performance_stats(rows)
    total = stats["settled"]
    wins  = stats["wins"]
    prof  = stats["profit_units"]
    roi   = stats["roi_pct"]

    if total:
        lines.append(
            f"Resueltos: <b>{total}</b>  ·  "
            f"Ganados: <b>{wins}</b>  ·  "
            f"Profit: <b>{prof:+.2f}u</b>  ·  "
            f"ROI: <b>{roi:+.2f}%</b>"
        )
    lines.append("")

    # Individual results (most recent first, max _SHOW_MAX)
    shown = rows[:_SHOW_MAX]
    sep = "─" * 28
    for r in shown:
        fix = fix_map.get(r["fixture_id"], {})
        home = esc(team_map.get(fix.get("home_team_id"), "Local"))
        away = esc(team_map.get(fix.get("away_team_id"), "Visitante"))
        league = esc(cs_map.get(fix.get("league_id"), ""))
        ko = (fix.get("kickoff_at") or "")[:10]
        icon = _result_icon(r["result_status"])
        market = r.get("market_key", "?")
        sel = esc(_sel(market, r.get("selection", "?")))
        mkt = _mkt(market)
        odd = float(r.get("odd_taken") or 0)
        profit = float(r.get("profit_units") or 0)
        settled_at = (r.get("settled_at") or "")[:10]

        lines.append(sep)
        lines.append(f"{icon} <b>{home} vs {away}</b>")
        if league:
            lines.append(f"   {league} · {ko}")
        lines.append(f"   {mkt} — {sel}  ·  Cuota: <b>{odd:.2f}</b>")
        result_str = r["result_status"].upper()
        lines.append(f"   <b>{result_str}</b>  ·  Profit: <b>{profit:+.4f} u</b>  ·  {settled_at}")

    if len(rows) > _SHOW_MAX:
        lines.append(sep)
        lines.append(f"<i>...y {len(rows) - _SHOW_MAX} más. Usa /rendimiento para el resumen completo.</i>")

    lines.append("")
    lines.append("Ver métricas: /rendimiento")
    return "\n".join(lines)
