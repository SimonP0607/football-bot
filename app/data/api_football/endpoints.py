"""High-level API-Football v3 fetch functions.

Each function maps to one API endpoint and returns parsed, ready-to-use data.
All functions are async and use the shared api_client singleton.

Endpoint groups:
  Reference  — bookmakers, bet types (Phase A)
  Leagues    — league metadata + coverage (Phase B)
  Fixtures   — match schedule (Phase C)
  Context    — standings, team stats, injuries, provider predictions (Phase C)
  Odds       — prematch odds with pagination (Phase C/D)
  Lineups    — starting XI near kickoff (Phase D)
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .client import APIFootballError, api_client

# Local cache for /timezone list — avoids calling it on every sync run
_TIMEZONE_CACHE_FILE = Path(__file__).resolve().parents[3] / ".timezone_cache.json"
_TIMEZONE_CACHE_TTL_DAYS = 7

logger = logging.getLogger(__name__)

# ── Market mapping ────────────────────────────────────────────────────────────
# Internal code → API-Football bet name (for /odds filtering)
MARKET_NAME_MAP: dict[str, str] = {
    "1X2":  "Match Winner",
    "OU25": "Goals Over/Under",
    "BTTS": "Both Teams Score",
}

# Selections to keep for Goals Over/Under (only the 2.5 line)
OU25_SELECTIONS: set[str] = {"Over 2.5", "Under 2.5"}


# ── Phase A: Reference data ───────────────────────────────────────────────────


async def fetch_bookmakers() -> list[dict]:
    """Return all bookmakers from /odds/bookmakers.

    Each item: {"id": int, "name": str}
    """
    data = await api_client.get("/odds/bookmakers")
    items: list[dict] = data.get("response", [])
    logger.info("Bookmakers recibidos: %d", len(items))
    return items


async def fetch_bet_types() -> list[dict]:
    """Return all prematch bet types from /odds/bets.

    Each item: {"id": int, "name": str}
    NOTE: These IDs are distinct from /odds/live/bets — never mix them.
    """
    data = await api_client.get("/odds/bets")
    items: list[dict] = data.get("response", [])
    logger.info("Tipos de apuesta (prematch) recibidos: %d", len(items))
    return items


async def validate_timezone(tz: str) -> bool:
    """Return True if the given IANA timezone string is recognised by the API.

    Uses a local JSON cache (TTL: 7 days) to avoid calling /timezone on every
    sync run.  If the API call fails for any reason the error is reported clearly
    and the function returns True so the rest of the sync is not blocked.
    """
    timezones = _load_timezone_cache()
    if timezones is None:
        try:
            data = await api_client.get("/timezone")
            timezones = data.get("response", [])
            if timezones:
                _save_timezone_cache(timezones)
        except APIFootballError as exc:
            _log_timezone_api_error(exc)
            return True  # Don't block sync on timezone validation failure
        except Exception as exc:
            logger.error(
                "No se pudo validar timezone contra API-Football: %s\n"
                "  → Revisa conectividad de red y API_FOOTBALL_KEY en .env",
                exc,
            )
            return True

    valid = tz in timezones
    if not valid:
        logger.warning("Timezone '%s' no reconocida por API-Football", tz)
    return valid


def _load_timezone_cache() -> list[str] | None:
    """Return cached timezone list if it exists and has not expired."""
    if not _TIMEZONE_CACHE_FILE.exists():
        return None
    try:
        data: dict = json.loads(_TIMEZONE_CACHE_FILE.read_text(encoding="utf-8"))
        saved_at = datetime.fromisoformat(data["saved_at"])
        if datetime.now(timezone.utc) - saved_at > timedelta(days=_TIMEZONE_CACHE_TTL_DAYS):
            logger.debug("Timezone cache expirada — se refrescará desde /timezone")
            return None
        timezones: list[str] = data["timezones"]
        logger.debug("Timezone cache válida (%d zonas)", len(timezones))
        return timezones
    except Exception:
        return None


def _save_timezone_cache(timezones: list[str]) -> None:
    """Persist timezone list to the local cache file."""
    try:
        _TIMEZONE_CACHE_FILE.write_text(
            json.dumps(
                {"saved_at": datetime.now(timezone.utc).isoformat(), "timezones": timezones},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        logger.debug("Timezone cache guardada (%d zonas)", len(timezones))
    except Exception as exc:
        logger.debug("No se pudo guardar timezone cache: %s", exc)


def _log_timezone_api_error(exc: APIFootballError) -> None:
    """Log a clear, actionable message when /timezone fails with an API error."""
    errors = exc.errors
    error_str = str(errors).lower()
    if "application key" in error_str or (
        isinstance(errors, dict) and "token" in errors
    ):
        logger.error(
            "Error de autenticación al llamar /timezone: %s\n"
            "  → API_FOOTBALL_KEY en .env es inválida o está vacía.\n"
            "  → Diagnóstico: python scripts/check_api_football.py",
            errors,
        )
    else:
        logger.error("Error de API-Football al llamar /timezone: %s", errors)


# ── Phase B: League metadata + coverage ──────────────────────────────────────


async def fetch_league_coverage(league_id: int, season: int) -> dict | None:
    """Return the current-season coverage dict for a specific league.

    Fetches /leagues?id=<league_id>&season=<season> and extracts the
    coverage object from the matching season entry.

    Returns None if the league or season is not found.
    """
    data = await api_client.get(
        "/leagues", params={"id": league_id, "season": season}
    )
    response: list[dict] = data.get("response", [])
    if not response:
        logger.warning(
            "Ningún resultado para /leagues?id=%s&season=%s", league_id, season
        )
        return None

    entry = response[0]
    seasons: list[dict] = entry.get("seasons", [])
    league_info = entry.get("league", {})
    country_info = entry.get("country", {})

    for s in seasons:
        if s.get("year") == season:
            coverage = s.get("coverage", {})
            logger.info(
                "Coverage para league=%s season=%s: standings=%s injuries=%s predictions=%s odds=%s",
                league_id, season,
                coverage.get("standings"),
                coverage.get("injuries"),
                coverage.get("predictions"),
                coverage.get("odds"),
            )
            return {
                "coverage": coverage,
                "season_start": s.get("start"),
                "season_end": s.get("end"),
                "current": s.get("current", False),
                "type": league_info.get("type"),
                "name": league_info.get("name"),
                "country": country_info.get("name"),
            }

    logger.warning(
        "Temporada %s no encontrada en /leagues?id=%s", season, league_id
    )
    return None


async def fetch_active_leagues(league_ids: list[int] | None = None) -> list[dict]:
    """Discover currently-running leagues and their active season via /leagues?current=true.

    Makes a single API call that returns all globally-active leagues, then
    filters client-side to the given league_ids if provided.

    Args:
        league_ids: If provided, only return leagues whose provider ID is in this list.
                    Pass None to return all currently-active leagues.

    Returns:
        List of dicts, one per active league:
            {league_id, name, country, type, season, season_start, season_end,
             current, coverage}
        Only leagues with a season entry where current=true are included.
    """
    data = await api_client.get("/leagues", params={"current": "true"})
    response: list[dict] = data.get("response", [])

    filter_set: set[int] | None = set(league_ids) if league_ids else None
    result: list[dict] = []

    for entry in response:
        league_info = entry.get("league", {})
        country_info = entry.get("country", {})
        league_id: int = league_info.get("id", 0)

        if filter_set and league_id not in filter_set:
            continue

        # Find the entry marked current=true in this league's seasons list
        seasons: list[dict] = entry.get("seasons", [])
        current_season = next((s for s in seasons if s.get("current")), None)
        if not current_season:
            logger.debug("Liga %s sin entrada current=true en /leagues — omitida", league_id)
            continue

        result.append({
            "league_id": league_id,
            "name": league_info.get("name"),
            "country": country_info.get("name"),
            "type": league_info.get("type"),
            "season": current_season.get("year"),
            "season_start": current_season.get("start"),
            "season_end": current_season.get("end"),
            "current": True,
            "coverage": current_season.get("coverage", {}),
        })

    logger.info(
        "Ligas activas via /leagues?current=true: %d%s",
        len(result),
        f" (de {len(response)} totales, filtradas a IDs solicitados)" if filter_set else "",
    )
    return result


# ── Phase C: Fixtures ─────────────────────────────────────────────────────────


async def fetch_fixtures_by_ids(provider_fixture_ids: list[int]) -> list[dict]:
    """Fetch current status + goals for multiple fixture IDs from /fixtures?ids=.

    Batches up to 20 IDs per request. Used by settle_results.py for settlement.
    Returns list of {provider_fixture_id, status_short, goals_home, goals_away}.
    Only costs 1 API call per 20 fixtures.
    """
    if not provider_fixture_ids:
        return []
    results: list[dict] = []
    for i in range(0, len(provider_fixture_ids), 20):
        batch = provider_fixture_ids[i : i + 20]
        ids_str = "-".join(str(fid) for fid in batch)
        logger.info("fetch_fixtures_by_ids: batch %d ids", len(batch))
        data = await api_client.get("/fixtures", params={"ids": ids_str})
        for item in data.get("response", []):
            fix_info = item.get("fixture", {})
            goals = item.get("goals", {})
            results.append({
                "provider_fixture_id": fix_info.get("id"),
                "status_short": fix_info.get("status", {}).get("short"),
                "goals_home": goals.get("home"),
                "goals_away": goals.get("away"),
            })
    logger.info("fetch_fixtures_by_ids: %d total results", len(results))
    return results


async def fetch_fixture_live_states(provider_fixture_ids: list[int]) -> list[dict]:
    """Same as fetch_fixtures_by_ids but also returns status_elapsed.

    Returns list of {provider_fixture_id, status_short, status_elapsed, goals_home, goals_away}.
    Batches up to 20 IDs per request.
    """
    if not provider_fixture_ids:
        return []
    results: list[dict] = []
    for i in range(0, len(provider_fixture_ids), 20):
        batch = provider_fixture_ids[i : i + 20]
        ids_str = "-".join(str(fid) for fid in batch)
        logger.info("fetch_fixture_live_states: batch %d ids", len(batch))
        data = await api_client.get("/fixtures", params={"ids": ids_str})
        for item in data.get("response", []):
            fix_info = item.get("fixture", {})
            goals = item.get("goals", {})
            status = fix_info.get("status", {})
            results.append({
                "provider_fixture_id": fix_info.get("id"),
                "status_short": status.get("short"),
                "status_elapsed": status.get("elapsed"),
                "goals_home": goals.get("home"),
                "goals_away": goals.get("away"),
            })
    logger.info("fetch_fixture_live_states: %d total results", len(results))
    return results


async def fetch_fixtures(
    date: str, league_id: int, season: int, timezone: str | None = None
) -> list[dict]:
    """Return raw fixture items from /fixtures for a date/league/season.

    Args:
        date: Date string in YYYY-MM-DD format.
        league_id: API-Football league ID.
        season: Four-digit season year (e.g. 2025).
        timezone: IANA timezone for returned dates. Defaults to UTC.

    Returns:
        List of fixture dicts as returned by the /fixtures endpoint.
    """
    params: dict = {"date": date, "league": league_id, "season": season}
    if timezone:
        params["timezone"] = timezone
    logger.info(
        "Fetching fixtures — league=%s season=%s date=%s", league_id, season, date
    )
    data = await api_client.get("/fixtures", params=params)
    items: list[dict] = data.get("response", [])
    logger.info("Recibidos %d fixtures de API-Football", len(items))
    return items


# ── Phase C: Context enrichers ────────────────────────────────────────────────


async def fetch_standings(league_id: int, season: int) -> list[dict]:
    """Return the standings table for a league/season from /standings.

    Returns a flat list of standing entries (all groups merged).
    Each entry contains: rank, team id/name, points, played, wins, draws, losses,
    goals_for, goals_against, goal_diff, form, status.
    """
    data = await api_client.get(
        "/standings", params={"league": league_id, "season": season}
    )
    response: list[dict] = data.get("response", [])
    if not response:
        return []

    # Response is: [{league: {standings: [[...], [...]]}}]
    # Flatten all groups (regular, playoff, etc.)
    all_entries: list[dict] = []
    for item in response:
        league_data = item.get("league", {})
        for group in league_data.get("standings", []):
            all_entries.extend(group)

    logger.debug(
        "Standings league=%s season=%s → %d equipos", league_id, season, len(all_entries)
    )
    return all_entries


async def fetch_team_statistics(
    team_id: int, league_id: int, season: int
) -> dict:
    """Return team performance statistics from /teams/statistics.

    Returns a cleaned dict with form, fixture splits, goals, clean sheets, etc.
    Returns an empty dict if the response is missing or malformed.
    """
    data = await api_client.get(
        "/teams/statistics",
        params={"team": team_id, "league": league_id, "season": season},
    )
    resp = data.get("response", {})
    if not resp:
        return {}

    # Extract the most useful fields for our model
    return {
        "form": resp.get("form", ""),
        "fixtures": resp.get("fixtures", {}),
        "goals": resp.get("goals", {}),
        "clean_sheet": resp.get("clean_sheet", {}),
        "failed_to_score": resp.get("failed_to_score", {}),
        "penalty": resp.get("penalty", {}),
    }


async def fetch_injuries(fixture_id: int) -> list[dict]:
    """Return injury list for a fixture from /injuries.

    Returns a list of dicts with player id/name, type (injured/suspended), team id.
    player_id may be None for some API responses.
    """
    data = await api_client.get("/injuries", params={"fixture": fixture_id})
    items: list[dict] = data.get("response", [])
    result = []
    for item in items:
        player = item.get("player", {})
        team = item.get("team", {})
        result.append({
            "player_id": player.get("id"),
            "player_name": player.get("name"),
            "type": player.get("type"),
            "reason": player.get("reason"),
            "team_id": team.get("id"),
            "team_name": team.get("name"),
        })
    logger.debug("Injuries fixture=%s → %d bajas", fixture_id, len(result))
    return result


async def fetch_provider_predictions(fixture_id: int) -> dict:
    """Return API-Football's own predictions for a fixture from /predictions.

    NOTE: These predictions are used as an auxiliary signal only, not as ground truth.
    They should reinforce or mildly penalise our model's output.

    Returns a dict with advice, percent, winner, goals, and under_over; or {} on error.
    """
    data = await api_client.get("/predictions", params={"fixture": fixture_id})
    response: list[dict] = data.get("response", [])
    if not response:
        return {}

    pred = response[0]
    teams = pred.get("teams", {})
    comparison = pred.get("comparison", {})
    goals = pred.get("goals", {})

    return {
        "winner_id": pred.get("winner", {}).get("id"),
        "winner_comment": pred.get("winner", {}).get("comment"),
        "advice": pred.get("advice", ""),
        "percent": pred.get("percent", {}),       # {"home": "60%", "draw": "20%", "away": "20%"}
        "goals_home": goals.get("home"),
        "goals_away": goals.get("away"),
        "under_over": pred.get("under_over"),     # e.g. "+2.5" or "-2.5"
        "comparison": {                            # statistical comparison per dimension
            "form": comparison.get("form", {}),
            "att": comparison.get("att", {}),
            "def": comparison.get("def", {}),
            "poisson_distribution": comparison.get("poisson_distribution", {}),
            "h2h": comparison.get("h2h", {}),
            "goals": comparison.get("goals", {}),
            "total": comparison.get("total", {}),
        },
    }


# ── Phase C/D: Odds with pagination ──────────────────────────────────────────


async def fetch_odds(
    fixture_id: int,
    markets: list[str],
    bookmaker_id: int | None = None,
) -> list[dict]:
    """Return parsed odds rows for a fixture, filtered to the requested markets.

    Fetches all pages from /odds (paginated), filters to the requested market
    names, and returns flat rows ready for upserting.

    Each returned dict:
        {
            "bookmaker_id": int,
            "bookmaker_name": str,
            "bet_id": int,
            "market": str,       # internal code ("1X2", "OU25", "BTTS")
            "selection": str,
            "odd": float,
        }

    Args:
        fixture_id: API-Football fixture ID.
        markets: Internal market codes to keep.
        bookmaker_id: Optional — if set, only parse odds from this bookmaker.
    """
    logger.info(
        "Fetching odds — fixture=%s markets=%s bk=%s",
        fixture_id, markets, bookmaker_id,
    )

    api_to_internal = {v: k for k, v in MARKET_NAME_MAP.items() if k in markets}
    params: dict = {"fixture": fixture_id}
    if bookmaker_id:
        params["bookmaker"] = bookmaker_id

    rows: list[dict] = []
    async for page_items in api_client.get_paginated("/odds", params):
        for item in page_items:
            for bk in item.get("bookmakers", []):
                bk_id: int = bk.get("id", 0)
                bk_name: str = bk.get("name", "Unknown")
                for bet in bk.get("bets", []):
                    api_market_name: str = bet.get("name", "")
                    internal_market = api_to_internal.get(api_market_name)
                    if not internal_market:
                        continue

                    bet_id: int = bet.get("id", 0)
                    for value in bet.get("values", []):
                        selection: str = value.get("value", "")
                        if internal_market == "OU25" and selection not in OU25_SELECTIONS:
                            continue
                        try:
                            odd = float(value.get("odd", 0))
                        except (TypeError, ValueError):
                            continue
                        if odd <= 1.0:
                            continue

                        rows.append({
                            "bookmaker_id": bk_id,
                            "bookmaker_name": bk_name,
                            "bet_id": bet_id,
                            "market": internal_market,
                            "selection": selection,
                            "odd": odd,
                        })

    logger.info(
        "Parseados %d registros de cuotas para fixture=%s", len(rows), fixture_id
    )
    return rows


# ── Phase D: Lineups ──────────────────────────────────────────────────────────


async def fetch_lineups(fixture_id: int) -> dict:
    """Return starting lineups for a fixture from /fixtures/lineups.

    Returns a dict with "home" and "away" keys, each containing:
        {"team_id": int, "formation": str, "start_xi": [player_name, ...], "coach": str}
    Returns {} if lineups are not yet available.
    """
    data = await api_client.get("/fixtures/lineups", params={"fixture": fixture_id})
    response: list[dict] = data.get("response", [])
    if not response:
        logger.debug("Lineups fixture=%s: sin datos (aún no publicados)", fixture_id)
        return {}

    result = {}
    for entry in response:
        team = entry.get("team", {})
        team_id = team.get("id")
        side = "home" if len(result) == 0 else "away"
        result[side] = {
            "team_id": team_id,
            "team_name": team.get("name"),
            "formation": entry.get("formation"),
            "start_xi": [
                p.get("player", {}).get("name")
                for p in entry.get("startXI", [])
                if p.get("player", {}).get("name")
            ],
            "coach": entry.get("coach", {}).get("name"),
        }

    logger.debug(
        "Lineups fixture=%s → %s", fixture_id,
        {k: v.get("formation") for k, v in result.items()}
    )
    return result


# ── Phase 3: Entity catalog ───────────────────────────────────────────────────


async def fetch_teams(league_id: int, season: int) -> list[dict]:
    """Return teams for a league/season from /teams.

    Each item includes team identity (id, name, code, country, national, logo,
    founded) and venue info (id, name, city, capacity).
    """
    data = await api_client.get("/teams", params={"league": league_id, "season": season})
    items: list[dict] = data.get("response", [])
    result: list[dict] = []
    for item in items:
        team = item.get("team", {})
        venue = item.get("venue", {})
        tid = team.get("id")
        if not tid:
            continue
        result.append({
            "provider_team_id": tid,
            "name": team.get("name", ""),
            "code": team.get("code"),
            "country": team.get("country"),
            "founded": team.get("founded"),
            "is_national": bool(team.get("national", False)),
            "logo": team.get("logo"),
            "venue_id": venue.get("id"),
            "venue_name": venue.get("name"),
            "venue_city": venue.get("city"),
            "venue_capacity": venue.get("capacity"),
        })
    logger.debug("fetch_teams league=%s season=%s → %d equipos", league_id, season, len(result))
    return result


async def fetch_team_by_id(team_id: int) -> dict | None:
    """Return identity info for a single team from /teams?id=.

    Returns a flat dict or None if not found.
    """
    data = await api_client.get("/teams", params={"id": team_id})
    items: list[dict] = data.get("response", [])
    if not items:
        return None
    item = items[0]
    team = item.get("team", {})
    venue = item.get("venue", {})
    return {
        "provider_team_id": team.get("id"),
        "name": team.get("name", ""),
        "code": team.get("code"),
        "country": team.get("country"),
        "founded": team.get("founded"),
        "is_national": bool(team.get("national", False)),
        "logo": team.get("logo"),
        "venue_id": venue.get("id"),
        "venue_name": venue.get("name"),
        "venue_city": venue.get("city"),
        "venue_capacity": venue.get("capacity"),
    }


async def fetch_players_squad(team_id: int) -> list[dict]:
    """Return basic squad list for a team from /players/squads.

    Cheap endpoint (1 call per team, no pagination). Returns minimal player
    info: id, name, age, number, position, photo. No career statistics.
    """
    data = await api_client.get("/players/squads", params={"team": team_id})
    items: list[dict] = data.get("response", [])
    players: list[dict] = []
    for item in items:
        for p in item.get("players", []):
            pid = p.get("id")
            if not pid:
                continue
            players.append({
                "provider_player_id": pid,
                "name": p.get("name", ""),
                "age": p.get("age"),
                "number": p.get("number"),
                "position": p.get("position"),
                "photo": p.get("photo"),
            })
    logger.debug("fetch_players_squad team=%s → %d jugadores", team_id, len(players))
    return players


async def fetch_players_by_team_season(
    team_id: int, season: int, page: int = 1
) -> tuple[list[dict], int]:
    """Return detailed players for a team/season from /players (paginated).

    Returns (players_list, total_pages). Each player includes full identity
    plus career stats for the season. Use sparingly — costs 1 call per page,
    typically 20-25 players per page.
    """
    data = await api_client.get(
        "/players", params={"team": team_id, "season": season, "page": page}
    )
    paging = data.get("paging", {})
    total_pages: int = paging.get("total", 1)
    items: list[dict] = data.get("response", [])
    players: list[dict] = []
    for item in items:
        p = item.get("player", {})
        birth = p.get("birth", {})
        pid = p.get("id")
        if not pid:
            continue
        players.append({
            "provider_player_id": pid,
            "name": p.get("name", ""),
            "firstname": p.get("firstname"),
            "lastname": p.get("lastname"),
            "age": p.get("age"),
            "birth_date": birth.get("date"),
            "birth_place": birth.get("place"),
            "birth_country": birth.get("country"),
            "nationality": p.get("nationality"),
            "height": p.get("height"),
            "weight": p.get("weight"),
            "injured": bool(p.get("injured", False)),
            "photo": p.get("photo"),
        })
    logger.debug(
        "fetch_players_by_team_season team=%s season=%s page=%s/%s → %d",
        team_id, season, page, total_pages, len(players),
    )
    return players, total_pages


# ── Phase 11: Player Intelligence ────────────────────────────────────────────


async def fetch_fixture_player_stats(provider_fixture_id: int) -> list[dict]:
    """Return per-player stats for a fixture from /fixtures/players.

    Each item includes player identity, team, and full statistical breakdown.
    Returns list of raw team blocks; parse with _parse_fixture_player_block().
    """
    data = await api_client.get(
        "/fixtures/players", params={"fixture": provider_fixture_id}
    )
    items: list[dict] = data.get("response", [])
    logger.debug(
        "fetch_fixture_player_stats fixture=%s → %d team blocks",
        provider_fixture_id, len(items),
    )
    return items


def parse_fixture_player_stats(
    raw_blocks: list[dict],
    provider_fixture_id: int,
    fixture_id: int | None = None,
    league_id: int | None = None,
    season: int | None = None,
) -> list[dict]:
    """Flatten /fixtures/players response into individual player rows.

    Each returned dict maps directly to the player_fixture_stats table.
    """
    rows: list[dict] = []
    for block in raw_blocks:
        team = block.get("team", {})
        team_id = team.get("id")
        team_name = team.get("name", "")
        for entry in block.get("players", []):
            p = entry.get("player", {})
            stats_list = entry.get("statistics", [])
            s = stats_list[0] if stats_list else {}

            player_id = p.get("id")
            if not player_id:
                continue

            games = s.get("games", {})
            shots = s.get("shots", {})
            goals = s.get("goals", {})
            passes = s.get("passes", {})
            tackles = s.get("tackles", {})
            duels = s.get("duels", {})
            dribbles = s.get("dribbles", {})
            fouls = s.get("fouls", {})
            cards = s.get("cards", {})
            penalty = s.get("penalty", {})

            def _i(val) -> int:
                try:
                    return int(val) if val is not None else 0
                except (TypeError, ValueError):
                    return 0

            def _f(val) -> float | None:
                try:
                    return float(val) if val is not None else None
                except (TypeError, ValueError):
                    return None

            import json as _json
            rows.append({
                "provider_fixture_id": provider_fixture_id,
                "fixture_id": fixture_id,
                "league_id": league_id,
                "season": season,
                "team_id": team_id,
                "player_id": player_id,
                "player_name": p.get("name", ""),
                "team_name": team_name,
                "position": games.get("position"),
                "minutes": _i(games.get("minutes")),
                "rating": _f(games.get("rating")),
                "captain": bool(games.get("captain", False)),
                "substitute": bool(games.get("substitute", False)),
                "offsides": _i(s.get("offsides")),
                "shots_total": _i(shots.get("total")),
                "shots_on": _i(shots.get("on")),
                "goals_total": _i(goals.get("total")),
                "goals_conceded": _i(goals.get("conceded")),
                "assists": _i(goals.get("assists")),
                "saves": _i(goals.get("saves")),
                "passes_total": _i(passes.get("total")),
                "passes_key": _i(passes.get("key")),
                "passes_accuracy": _f(passes.get("accuracy")),
                "tackles_total": _i(tackles.get("total")),
                "tackles_blocks": _i(tackles.get("blocks")),
                "tackles_interceptions": _i(tackles.get("interceptions")),
                "duels_total": _i(duels.get("total")),
                "duels_won": _i(duels.get("won")),
                "dribbles_attempts": _i(dribbles.get("attempts")),
                "dribbles_success": _i(dribbles.get("success")),
                "fouls_drawn": _i(fouls.get("drawn")),
                "fouls_committed": _i(fouls.get("committed")),
                "cards_yellow": _i(cards.get("yellow")),
                "cards_red": _i(cards.get("red")),
                "penalty_won": _i(penalty.get("won")),
                "penalty_committed": _i(penalty.get("committed")),
                "penalty_scored": _i(penalty.get("scored")),
                "penalty_missed": _i(penalty.get("missed")),
                "penalty_saved": _i(penalty.get("saved")),
                "raw_json": _json.dumps(entry),
            })
    return rows


async def fetch_players_by_league_season(
    league_id: int,
    season: int,
    page: int = 1,
) -> tuple[list[dict], int]:
    """Return players for a league/season from /players (paginated).

    Returns (players_list, total_pages). Each player has identity + season stats.
    Budget-heavy: use sparingly, max 1 page at a time.
    """
    data = await api_client.get(
        "/players", params={"league": league_id, "season": season, "page": page}
    )
    paging = data.get("paging", {})
    total_pages: int = max(paging.get("total", 1), 1)
    items: list[dict] = data.get("response", [])
    players = []
    for item in items:
        p = item.get("player", {})
        pid = p.get("id")
        if not pid:
            continue
        players.append({
            "provider_player_id": pid,
            "name": p.get("name", ""),
            "age": p.get("age"),
            "nationality": p.get("nationality"),
            "statistics": item.get("statistics", []),
        })
    logger.debug(
        "fetch_players_by_league_season league=%s season=%s page=%s/%s → %d",
        league_id, season, page, total_pages, len(players),
    )
    return players, total_pages


async def fetch_player_statistics(
    player_id: int,
    season: int | None = None,
    league_id: int | None = None,
) -> dict | None:
    """Return statistics for a single player from /players.

    Returns dict with player identity + statistics list, or None if not found.
    """
    params: dict = {"id": player_id}
    if season:
        params["season"] = season
    if league_id:
        params["league"] = league_id
    data = await api_client.get("/players", params=params)
    items = data.get("response", [])
    if not items:
        return None
    return items[0]  # {player: {...}, statistics: [...]}


async def fetch_top_scorers(league_id: int, season: int) -> list[dict]:
    """Return top scorers for a league/season from /players/topscorers."""
    data = await api_client.get(
        "/players/topscorers", params={"league": league_id, "season": season}
    )
    items = data.get("response", [])
    result = []
    for item in items:
        p = item.get("player", {})
        stats = item.get("statistics", [{}])[0]
        goals = stats.get("goals", {})
        result.append({
            "player_id": p.get("id"),
            "player_name": p.get("name", ""),
            "team_id": stats.get("team", {}).get("id"),
            "team_name": stats.get("team", {}).get("name", ""),
            "goals": goals.get("total", 0) or 0,
            "assists": goals.get("assists", 0) or 0,
            "appearances": stats.get("games", {}).get("appearences", 0) or 0,
        })
    logger.debug("fetch_top_scorers league=%s season=%s → %d", league_id, season, len(result))
    return result


async def fetch_top_assists(league_id: int, season: int) -> list[dict]:
    """Return top assist providers for a league/season from /players/topassists."""
    data = await api_client.get(
        "/players/topassists", params={"league": league_id, "season": season}
    )
    items = data.get("response", [])
    result = []
    for item in items:
        p = item.get("player", {})
        stats = item.get("statistics", [{}])[0]
        goals = stats.get("goals", {})
        result.append({
            "player_id": p.get("id"),
            "player_name": p.get("name", ""),
            "team_id": stats.get("team", {}).get("id"),
            "team_name": stats.get("team", {}).get("name", ""),
            "assists": goals.get("assists", 0) or 0,
            "goals": goals.get("total", 0) or 0,
            "appearances": stats.get("games", {}).get("appearences", 0) or 0,
        })
    logger.debug("fetch_top_assists league=%s season=%s → %d", league_id, season, len(result))
    return result


async def fetch_top_cards(league_id: int, season: int) -> list[dict]:
    """Return most-booked players for a league/season from /players/topyellowcards."""
    data = await api_client.get(
        "/players/topyellowcards", params={"league": league_id, "season": season}
    )
    items = data.get("response", [])
    result = []
    for item in items:
        p = item.get("player", {})
        stats = item.get("statistics", [{}])[0]
        cards = stats.get("cards", {})
        result.append({
            "player_id": p.get("id"),
            "player_name": p.get("name", ""),
            "team_id": stats.get("team", {}).get("id"),
            "team_name": stats.get("team", {}).get("name", ""),
            "yellow_cards": cards.get("yellow", 0) or 0,
            "red_cards": cards.get("red", 0) or 0,
            "appearances": stats.get("games", {}).get("appearences", 0) or 0,
        })
    logger.debug("fetch_top_cards league=%s season=%s → %d", league_id, season, len(result))
    return result


async def fetch_top_saves(league_id: int, season: int) -> list[dict]:
    """Return goalkeepers by saves for a league/season from /players/topredcards.

    NOTE: API-Football does not have a /players/topsaves endpoint.
    This queries /players/topsaves and gracefully returns [] if unavailable.
    """
    try:
        data = await api_client.get(
            "/players/topsaves", params={"league": league_id, "season": season}
        )
        items = data.get("response", [])
        result = []
        for item in items:
            p = item.get("player", {})
            stats = item.get("statistics", [{}])[0]
            goals = stats.get("goals", {})
            result.append({
                "player_id": p.get("id"),
                "player_name": p.get("name", ""),
                "team_id": stats.get("team", {}).get("id"),
                "team_name": stats.get("team", {}).get("name", ""),
                "saves": goals.get("saves", 0) or 0,
                "appearances": stats.get("games", {}).get("appearences", 0) or 0,
            })
        logger.debug("fetch_top_saves league=%s season=%s → %d", league_id, season, len(result))
        return result
    except Exception as exc:
        logger.debug("fetch_top_saves not available: %s", exc)
        return []


# ── Phase 12: Market Intelligence — Odds endpoints ───────────────────────────


async def fetch_odds_by_fixture(
    fixture_id: int,
    bookmaker: int | None = None,
    bet: int | None = None,
) -> list[dict]:
    """Return raw paginated odds from /odds for a specific fixture.

    Returns the full structured response items for market intelligence snapshot
    storage. Each item: {fixture, league, bookmakers: [{id, name, bets: [...]}]}
    """
    params: dict = {"fixture": fixture_id}
    if bookmaker:
        params["bookmaker"] = bookmaker
    if bet:
        params["bet"] = bet

    rows: list[dict] = []
    async for page_items in api_client.get_paginated("/odds", params):
        rows.extend(page_items)

    logger.debug("fetch_odds_by_fixture fixture=%s → %d items", fixture_id, len(rows))
    return rows


async def fetch_odds_by_league_date(
    league_id: int,
    season: int,
    date: str,
    bookmaker: int | None = None,
    bet: int | None = None,
) -> list[dict]:
    """Return raw odds from /odds for all fixtures in a league on a given date."""
    params: dict = {"league": league_id, "season": season, "date": date}
    if bookmaker:
        params["bookmaker"] = bookmaker
    if bet:
        params["bet"] = bet

    rows: list[dict] = []
    async for page_items in api_client.get_paginated("/odds", params):
        rows.extend(page_items)

    logger.debug(
        "fetch_odds_by_league_date league=%s season=%s date=%s → %d items",
        league_id, season, date, len(rows),
    )
    return rows


async def fetch_live_odds_by_fixture(
    fixture_id: int,
    bookmaker: int | None = None,
    bet: int | None = None,
) -> list[dict]:
    """Return live odds from /odds/live for a specific fixture."""
    params: dict = {"fixture": fixture_id}
    if bookmaker:
        params["bookmaker"] = bookmaker
    if bet:
        params["bet"] = bet

    data = await api_client.get("/odds/live", params=params)
    items: list[dict] = data.get("response", [])
    logger.debug("fetch_live_odds_by_fixture fixture=%s → %d items", fixture_id, len(items))
    return items


async def fetch_odds_bookmakers() -> list[dict]:
    """Return all bookmakers from /odds/bookmakers."""
    return await fetch_bookmakers()


async def fetch_odds_bets() -> list[dict]:
    """Return all prematch bet types from /odds/bets."""
    return await fetch_bet_types()


async def fetch_live_odds_bets() -> list[dict]:
    """Return all live bet types from /odds/live/bets."""
    data = await api_client.get("/odds/live/bets")
    items: list[dict] = data.get("response", [])
    logger.info("Live bet types recibidos: %d", len(items))
    return items


async def fetch_player_by_id(player_id: int, season: int | None = None) -> dict | None:
    """Return identity info for a single player from /players.

    Returns a flat dict or None if not found.
    """
    params: dict = {"id": player_id}
    if season:
        params["season"] = season
    data = await api_client.get("/players", params=params)
    items: list[dict] = data.get("response", [])
    if not items:
        return None
    item = items[0]
    p = item.get("player", {})
    birth = p.get("birth", {})
    return {
        "provider_player_id": p.get("id"),
        "name": p.get("name", ""),
        "firstname": p.get("firstname"),
        "lastname": p.get("lastname"),
        "age": p.get("age"),
        "birth_date": birth.get("date"),
        "birth_place": birth.get("place"),
        "birth_country": birth.get("country"),
        "nationality": p.get("nationality"),
        "height": p.get("height"),
        "weight": p.get("weight"),
        "injured": bool(p.get("injured", False)),
        "photo": p.get("photo"),
    }
