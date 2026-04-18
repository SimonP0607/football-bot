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

import logging
from .client import api_client

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
    """Return True if the given IANA timezone string is recognised by the API."""
    data = await api_client.get("/timezone")
    timezones: list[str] = data.get("response", [])
    valid = tz in timezones
    if not valid:
        logger.warning("Timezone '%s' no reconocida por API-Football", tz)
    return valid


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


# ── Phase C: Fixtures ─────────────────────────────────────────────────────────


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

    Returns a list of dicts with player name, type (injured/suspended), and team id.
    """
    data = await api_client.get("/injuries", params={"fixture": fixture_id})
    items: list[dict] = data.get("response", [])
    result = []
    for item in items:
        player = item.get("player", {})
        team = item.get("team", {})
        result.append({
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
