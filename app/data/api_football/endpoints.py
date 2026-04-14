"""High-level API-Football fetch functions used by the sync service."""

import logging
from .client import api_client

logger = logging.getLogger(__name__)

# Internal market name → API-Football bet name
MARKET_NAME_MAP: dict[str, str] = {
    "1X2": "Match Winner",
    "OU25": "Goals Over/Under",
    "BTTS": "Both Teams Score",
}

# Selections to keep for Goals Over/Under (we only want the 2.5 line)
OU25_SELECTIONS: set[str] = {"Over 2.5", "Under 2.5"}


async def fetch_fixtures(date: str, league_id: int, season: int) -> list[dict]:
    """Return raw fixture items from API-Football for a given date/league/season.

    Args:
        date: Date string in YYYY-MM-DD format (local or UTC).
        league_id: API-Football league ID.
        season: Four-digit season year (e.g. 2024).

    Returns:
        List of fixture dicts as returned by API-Football's ``/fixtures`` endpoint.
    """
    logger.info("Fetching fixtures — league=%s season=%s date=%s", league_id, season, date)
    data = await api_client.get(
        "/fixtures",
        params={"date": date, "league": league_id, "season": season},
    )
    items: list[dict] = data.get("response", [])
    logger.info("Recibidos %d fixtures de API-Football", len(items))
    return items


async def fetch_odds(fixture_id: int, markets: list[str]) -> list[dict]:
    """Return parsed odds rows for a fixture, filtered to the requested markets.

    Each returned dict has the shape::

        {
            "bookmaker": str,
            "market": str,      # internal name (e.g. "1X2")
            "selection": str,
            "odd": float,
        }

    Args:
        fixture_id: API-Football fixture ID.
        markets: List of internal market codes (e.g. ``["1X2", "OU25", "BTTS"]``).

    Returns:
        Flat list of odds rows ready for upserting.
    """
    logger.info("Fetching odds — fixture=%s markets=%s", fixture_id, markets)
    data = await api_client.get("/odds", params={"fixture": fixture_id})
    response: list[dict] = data.get("response", [])

    # Build reverse map: API name → internal name
    api_to_internal = {v: k for k, v in MARKET_NAME_MAP.items() if k in markets}

    rows: list[dict] = []
    for item in response:
        for bookmaker in item.get("bookmakers", []):
            bk_name: str = bookmaker.get("name", "Unknown")
            for bet in bookmaker.get("bets", []):
                api_market_name: str = bet.get("name", "")
                internal_market = api_to_internal.get(api_market_name)
                if not internal_market:
                    continue  # market not requested

                for value in bet.get("values", []):
                    selection: str = value.get("value", "")
                    # For OU25 only keep 2.5 line
                    if internal_market == "OU25" and selection not in OU25_SELECTIONS:
                        continue
                    try:
                        odd = float(value.get("odd", 0))
                    except (TypeError, ValueError):
                        continue
                    if odd <= 1.0:
                        continue  # invalid odd

                    rows.append(
                        {
                            "bookmaker": bk_name,
                            "market": internal_market,
                            "selection": selection,
                            "odd": odd,
                        }
                    )

    logger.info("Parseados %d registros de cuotas para fixture=%s", len(rows), fixture_id)
    return rows
