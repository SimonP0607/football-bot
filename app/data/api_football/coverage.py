"""Coverage cache and helpers for API-Football league/season coverage.

Coverage tells us which endpoints are available for a given league+season
combination. It is fetched during Phase B (bootstrap sync) from /leagues and
stored in leagues.coverage (jsonb).

Before calling standings, injuries, predictions, or odds for a fixture, the
sync service reads coverage from DB and skips the call if the flag is false,
logging a clear trace so the omission is auditable.

Coverage structure (as returned by API-Football /leagues):
    {
        "fixtures": {
            "events": true,
            "lineups": true,
            "statistics_fixtures": true,
            "statistics_players": true
        },
        "standings": true,
        "players": true,
        "top_scorers": true,
        "top_assists": true,
        "top_cards": true,
        "injuries": true,
        "predictions": true,
        "odds": true
    }
"""

import logging

logger = logging.getLogger(__name__)


def check(coverage: dict, flag: str, league_id: int, context: str = "") -> bool:
    """Return True if the given coverage flag is available for this league.

    Logs a debug trace when the call is skipped so every omission is visible.

    Args:
        coverage: The jsonb coverage dict stored in leagues.coverage.
        flag: Top-level flag name, e.g. "standings", "injuries", "predictions", "odds".
        league_id: Used for the log message only.
        context: Optional extra context for the log (e.g. fixture_id).
    """
    if not coverage:
        logger.debug(
            "coverage desconocida para league=%s — omitiendo %s%s",
            league_id, flag, f" (fixture={context})" if context else "",
        )
        return False

    available = bool(coverage.get(flag, False))
    if not available:
        logger.debug(
            "coverage.%s=false para league=%s — llamada omitida%s",
            flag, league_id, f" (fixture={context})" if context else "",
        )
    return available


def check_lineups(coverage: dict, league_id: int, fixture_id: int | None = None) -> bool:
    """Check specifically for fixtures.lineups coverage."""
    fixtures_cov = coverage.get("fixtures", {}) if coverage else {}
    available = bool(fixtures_cov.get("lineups", False))
    if not available:
        logger.debug(
            "coverage.fixtures.lineups=false para league=%s — lineups omitidos%s",
            league_id, f" (fixture={fixture_id})" if fixture_id else "",
        )
    return available


def check_fixture_stats(coverage: dict, league_id: int) -> bool:
    """Check specifically for fixtures.statistics_fixtures coverage."""
    fixtures_cov = coverage.get("fixtures", {}) if coverage else {}
    return bool(fixtures_cov.get("statistics_fixtures", False))


def empty() -> dict:
    """Return a fully-false coverage dict (safe default when coverage is unknown)."""
    return {
        "fixtures": {
            "events": False,
            "lineups": False,
            "statistics_fixtures": False,
            "statistics_players": False,
        },
        "standings": False,
        "players": False,
        "top_scorers": False,
        "top_assists": False,
        "top_cards": False,
        "injuries": False,
        "predictions": False,
        "odds": False,
    }
