"""Supabase repository for leagues, teams, and fixtures."""

import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.data.repositories.supabase_client import get_supabase
from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Leagues ───────────────────────────────────────────────────────────────────


def upsert_league(
    provider_league_id: int, name: str, country: str | None, season: int
) -> dict:
    """Insert or update a league. Returns the stored row."""
    client = get_supabase()
    result = (
        client.table("leagues")
        .upsert(
            {
                "provider_league_id": provider_league_id,
                "name": name,
                "country": country,
                "season": season,
            },
            on_conflict="provider_league_id,season",
        )
        .execute()
    )
    row = result.data[0] if result.data else {}
    logger.debug("upsert_league id=%s → internal_id=%s", provider_league_id, row.get("id"))
    return row


# ── Teams ─────────────────────────────────────────────────────────────────────


def upsert_team(
    provider_team_id: int, name: str, country: str | None
) -> dict:
    """Insert or update a team. Returns the stored row."""
    client = get_supabase()
    result = (
        client.table("teams")
        .upsert(
            {
                "provider_team_id": provider_team_id,
                "name": name,
                "country": country,
            },
            on_conflict="provider_team_id",
        )
        .execute()
    )
    row = result.data[0] if result.data else {}
    logger.debug("upsert_team id=%s → internal_id=%s", provider_team_id, row.get("id"))
    return row


# ── Fixtures ──────────────────────────────────────────────────────────────────


def upsert_fixture(
    provider_fixture_id: int,
    league_id: int,
    home_team_id: int,
    away_team_id: int,
    kickoff_at: str,
    status: str,
) -> dict:
    """Insert or update a fixture. Returns the stored row."""
    client = get_supabase()
    result = (
        client.table("fixtures")
        .upsert(
            {
                "provider_fixture_id": provider_fixture_id,
                "league_id": league_id,
                "home_team_id": home_team_id,
                "away_team_id": away_team_id,
                "kickoff_at": kickoff_at,
                "status": status,
            },
            on_conflict="provider_fixture_id",
        )
        .execute()
    )
    row = result.data[0] if result.data else {}
    logger.debug(
        "upsert_fixture provider_id=%s → internal_id=%s", provider_fixture_id, row.get("id")
    )
    return row


def get_fixtures_today() -> list[dict]:
    """Return all fixtures whose kickoff is within the current local calendar day.

    Uses the configured DEFAULT_TIMEZONE. The stored ``kickoff_at`` column is
    ``timestamptz`` (UTC), so the comparison is done with UTC-aware boundaries.
    """
    tz = ZoneInfo(settings.default_timezone)
    today_local = datetime.now(tz).date()
    day_start = datetime.combine(today_local, time.min).replace(tzinfo=tz).isoformat()
    day_end = datetime.combine(today_local, time.max).replace(tzinfo=tz).isoformat()

    client = get_supabase()
    result = (
        client.table("fixtures")
        .select("*")
        .gte("kickoff_at", day_start)
        .lte("kickoff_at", day_end)
        .order("kickoff_at")
        .execute()
    )
    return result.data or []


def get_fixture_by_internal_id(fixture_id: int) -> dict | None:
    """Return a single fixture by its internal (Supabase) ID, or None."""
    client = get_supabase()
    result = (
        client.table("fixtures")
        .select("*")
        .eq("id", fixture_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def get_teams_by_ids(team_ids: set[int]) -> dict[int, str]:
    """Return {team_id: team_name} for a set of internal IDs — single query."""
    if not team_ids:
        return {}
    client = get_supabase()
    result = (
        client.table("teams")
        .select("id, name")
        .in_("id", list(team_ids))
        .execute()
    )
    return {row["id"]: row["name"] for row in (result.data or [])}


def get_leagues_by_ids(league_ids: set[int]) -> dict[int, str]:
    """Return {league_id: league_name} for a set of internal IDs — single query."""
    if not league_ids:
        return {}
    client = get_supabase()
    result = (
        client.table("leagues")
        .select("id, name")
        .in_("id", list(league_ids))
        .execute()
    )
    return {row["id"]: row["name"] for row in (result.data or [])}
