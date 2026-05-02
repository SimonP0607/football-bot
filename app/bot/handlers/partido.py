"""Handler for /partido — detailed pre-match analysis of a specific fixture.

Usage:
  /partido 12345           — look up by API-Football fixture ID (provider ID)
  /partido Real Sociedad   — search today's fixtures by team name

The handler never makes live API calls — it reads from Supabase (synced data).
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware.auth import require_auth
from app.bot.middleware.db_guard import ensure_db_ready
from app.bot.formatters.pick_formatter import format_partido
from app.bot.utils import send_html
from app.data.repositories.fixture_repo import (
    get_fixture_by_provider_id,
    get_fixture_by_internal_id,
    search_fixtures_by_team_name,
    get_teams_by_ids,
    get_leagues_by_ids,
)
from app.services.prediction_service import prediction_service

logger = logging.getLogger(__name__)


@require_auth
async def partido_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/partido — Show full pre-match analysis for a fixture."""
    logger.info("/partido solicitado por user_id=%s", update.effective_user.id)

    if not await ensure_db_ready(update, context):
        return

    # Parse argument: fixture ID or team name query
    args = context.args or []
    query = " ".join(args).strip()

    if not query:
        await update.message.reply_text(
            "Uso: <code>/partido &lt;fixture_id o nombre de equipo&gt;</code>\n\n"
            "Ejemplos:\n"
            "  <code>/partido 1060362</code>\n"
            "  <code>/partido Atletico</code>",
            parse_mode="HTML",
        )
        return

    try:
        fixture = _resolve_fixture(query)
    except Exception as exc:
        logger.error("/partido: error buscando fixture '%s' — %s", query, exc)
        await update.message.reply_text("Error al buscar el partido. Revisa los logs.")
        return

    if fixture is None:
        await update.message.reply_text(
            f"No se encontró ningún partido para <code>{query}</code> hoy.\n\n"
            "Asegúrate de que el ID de fixture sea correcto o de que el equipo "
            "tenga un partido sincronizado para hoy.",
            parse_mode="HTML",
        )
        return

    if isinstance(fixture, list):
        # Multiple matches found — ask the user to be more specific
        lines = ["Encontré varios partidos. Usa el ID exacto:\n"]
        team_ids = {f["home_team_id"] for f in fixture} | {f["away_team_id"] for f in fixture}
        team_names = get_teams_by_ids(team_ids)
        for f in fixture:
            home = team_names.get(f["home_team_id"], "Local")
            away = team_names.get(f["away_team_id"], "Visitante")
            pid = f["provider_fixture_id"]
            lines.append(f"  <code>/partido {pid}</code> — {home} vs {away}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    try:
        candidates = prediction_service.analyze_fixture(fixture)

        team_ids = {fixture["home_team_id"], fixture["away_team_id"]}
        league_ids = {fixture["league_id"]}
        team_names = get_teams_by_ids(team_ids)
        league_names = get_leagues_by_ids(league_ids)

        # Phase 4: load availability from DuckDB (best-effort, never blocks)
        availability = None
        prematch     = None
        provider_fid = fixture.get("provider_fixture_id")
        try:
            from app.data.local.duckdb_client import get_local_db
            from app.data.local.availability_repo import get_availability_for_fixture
            _dconn = get_local_db()
            if provider_fid:
                availability = get_availability_for_fixture(_dconn, provider_fid)
        except Exception as _avail_exc:
            logger.debug("/partido: availability lookup failed — %s", _avail_exc)

        # Phase 6: load prematch intelligence from DuckDB (best-effort)
        try:
            from app.data.local.duckdb_client import get_local_db
            from app.data.local.prematch_repo import get_prematch_summary_for_fixture
            _dconn = get_local_db()
            if provider_fid:
                prematch = get_prematch_summary_for_fixture(_dconn, provider_fid)
        except Exception as _pm_exc:
            logger.debug("/partido: prematch lookup failed — %s", _pm_exc)

        # Phase 7: load live state from DuckDB (best-effort)
        live_state = None
        try:
            from app.data.local.duckdb_client import get_local_db
            from app.services.live_monitor_service import get_fixture_live_state
            _dconn = get_local_db()
            if provider_fid:
                live_state = get_fixture_live_state(_dconn, provider_fid)
        except Exception as _live_exc:
            logger.debug("/partido: live state lookup failed — %s", _live_exc)

        text = format_partido(
            fixture, candidates, team_names, league_names,
            availability=availability,
            prematch=prematch,
            live_state=live_state,
        )
    except Exception as exc:
        logger.error("/partido: error analizando fixture_id=%s — %s", fixture["id"], exc, exc_info=True)
        await update.message.reply_text(
            "Error al analizar el partido. Usa /estado para diagnosticar el sistema."
        )
        return

    await send_html(update.message, text)


def _resolve_fixture(query: str) -> dict | list | None:
    """Return one fixture dict, a list of candidates, or None.

    Resolution order:
      1. If query is a pure integer: look up by provider_fixture_id.
      2. Otherwise: search today's fixtures by team name.
    """
    if query.isdigit():
        fix = get_fixture_by_provider_id(int(query))
        return fix  # None if not found

    matches = search_fixtures_by_team_name(query, limit=5)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    return matches  # caller handles multiple results
