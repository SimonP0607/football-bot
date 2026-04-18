"""Supabase repository for leagues, teams, and fixtures."""

import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.data.repositories.supabase_client import get_supabase
from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Leagues ───────────────────────────────────────────────────────────────────


def upsert_league(
    provider_league_id: int,
    name: str,
    country: str | None,
    season: int,
    *,
    coverage: dict | None = None,
    season_start: str | None = None,
    season_end: str | None = None,
    current: bool = False,
    league_type: str | None = None,
) -> dict:
    """Insert or update a league row. Returns the stored row."""
    client = get_supabase()
    payload: dict = {
        "provider_league_id": provider_league_id,
        "name": name,
        "country": country,
        "season": season,
    }
    if coverage is not None:
        payload["coverage"] = coverage
    if season_start:
        payload["season_start"] = season_start
    if season_end:
        payload["season_end"] = season_end
    if current:
        payload["current"] = current
    if league_type:
        payload["type"] = league_type

    result = (
        client.table("leagues")
        .upsert(payload, on_conflict="provider_league_id,season")
        .execute()
    )
    row = result.data[0] if result.data else {}
    logger.debug(
        "upsert_league id=%s → internal_id=%s", provider_league_id, row.get("id")
    )
    return row


def get_league_coverage(provider_league_id: int, season: int) -> dict:
    """Return the coverage jsonb for a league+season, or {} if not found."""
    client = get_supabase()
    result = (
        client.table("leagues")
        .select("coverage")
        .eq("provider_league_id", provider_league_id)
        .eq("season", season)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0].get("coverage") or {}
    return {}


def get_active_leagues() -> list[dict]:
    """Return all active league rows ordered by name."""
    client = get_supabase()
    result = (
        client.table("leagues")
        .select("*")
        .eq("is_active", True)
        .order("name")
        .execute()
    )
    return result.data or []


def get_leagues_by_ids(league_ids: set[int]) -> dict[int, str]:
    """Return {league_id: league_name} for a set of internal IDs."""
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
    logger.debug(
        "upsert_team id=%s → internal_id=%s", provider_team_id, row.get("id")
    )
    return row


def get_teams_by_ids(team_ids: set[int]) -> dict[int, str]:
    """Return {team_id: team_name} for a set of internal IDs."""
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


# ── Fixtures ──────────────────────────────────────────────────────────────────


def upsert_fixture(
    provider_fixture_id: int,
    league_id: int,
    home_team_id: int,
    away_team_id: int,
    kickoff_at: str,
    status: str,
    *,
    timezone: str | None = None,
    date_local: str | None = None,
    status_short: str | None = None,
    status_long: str | None = None,
    elapsed: int | None = None,
    venue_id: int | None = None,
) -> dict:
    """Insert or update a fixture. Returns the stored row."""
    client = get_supabase()
    payload: dict = {
        "provider_fixture_id": provider_fixture_id,
        "league_id": league_id,
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "kickoff_at": kickoff_at,
        "status": status,
        "last_api_update": datetime.utcnow().isoformat() + "Z",
    }
    if timezone:
        payload["timezone"] = timezone
    if date_local:
        payload["date_local"] = date_local
    if status_short:
        payload["status_short"] = status_short
    if status_long:
        payload["status_long"] = status_long
    if elapsed is not None:
        payload["elapsed"] = elapsed
    if venue_id is not None:
        payload["venue_id"] = venue_id

    result = (
        client.table("fixtures")
        .upsert(payload, on_conflict="provider_fixture_id")
        .execute()
    )
    row = result.data[0] if result.data else {}
    logger.debug(
        "upsert_fixture provider_id=%s → internal_id=%s",
        provider_fixture_id, row.get("id"),
    )
    return row


def update_fixture_context(fixture_id: int, context_json: dict) -> None:
    """Store the match context blob (standings, form, stats, h2h, etc.) in fixtures."""
    client = get_supabase()
    try:
        client.table("fixtures").update(
            {"context_json": context_json}
        ).eq("id", fixture_id).execute()
        logger.debug("context_json actualizado para fixture_id=%s", fixture_id)
    except Exception as exc:
        logger.warning(
            "No se pudo guardar context_json para fixture_id=%s: %s", fixture_id, exc
        )


def get_fixtures_today() -> list[dict]:
    """Return all fixtures whose kickoff is within the current local calendar day.

    Uses the configured DEFAULT_TIMEZONE. The stored ``kickoff_at`` is
    timestamptz (UTC), so comparisons use UTC-aware boundaries.
    """
    tz = ZoneInfo(settings.default_timezone)
    today_local = datetime.now(tz).date()
    day_start = (
        datetime.combine(today_local, time.min).replace(tzinfo=tz).isoformat()
    )
    day_end = (
        datetime.combine(today_local, time.max).replace(tzinfo=tz).isoformat()
    )

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


def get_fixture_by_provider_id(provider_fixture_id: int) -> dict | None:
    """Return a fixture by its API-Football provider ID, or None."""
    client = get_supabase()
    result = (
        client.table("fixtures")
        .select("*")
        .eq("provider_fixture_id", provider_fixture_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def search_fixtures_by_team_name(query: str, limit: int = 5) -> list[dict]:
    """Search today's fixtures containing a team whose name matches the query.

    Uses case-insensitive ILIKE match against the teams table, then joins with
    today's fixtures. Returns a flat list of fixture rows.
    """
    client = get_supabase()
    # Find matching team internal IDs
    teams_result = (
        client.table("teams")
        .select("id")
        .ilike("name", f"%{query}%")
        .limit(20)
        .execute()
    )
    team_ids = [row["id"] for row in (teams_result.data or [])]
    if not team_ids:
        return []

    tz = ZoneInfo(settings.default_timezone)
    today_local = datetime.now(tz).date()
    day_start = (
        datetime.combine(today_local, time.min).replace(tzinfo=tz).isoformat()
    )
    day_end = (
        datetime.combine(today_local, time.max).replace(tzinfo=tz).isoformat()
    )

    result = (
        client.table("fixtures")
        .select("*")
        .gte("kickoff_at", day_start)
        .lte("kickoff_at", day_end)
        .in_("home_team_id", team_ids)
        .order("kickoff_at")
        .limit(limit)
        .execute()
    )
    # Also check away teams
    result2 = (
        client.table("fixtures")
        .select("*")
        .gte("kickoff_at", day_start)
        .lte("kickoff_at", day_end)
        .in_("away_team_id", team_ids)
        .order("kickoff_at")
        .limit(limit)
        .execute()
    )
    seen = set()
    combined = []
    for row in (result.data or []) + (result2.data or []):
        if row["id"] not in seen:
            seen.add(row["id"])
            combined.append(row)
    return combined[:limit]


def get_h2h_fixtures(
    home_team_id: int,
    away_team_id: int,
    limit: int = 5,
) -> list[dict]:
    """Return recent head-to-head fixtures between two teams (any order).

    Looks in the fixtures table for matches involving both teams (either as
    home or away), ordered newest first.
    """
    client = get_supabase()
    # Matches where (home=A and away=B) or (home=B and away=A)
    result = (
        client.table("fixtures")
        .select("kickoff_at, home_team_id, away_team_id, status")
        .or_(
            f"and(home_team_id.eq.{home_team_id},away_team_id.eq.{away_team_id}),"
            f"and(home_team_id.eq.{away_team_id},away_team_id.eq.{home_team_id})"
        )
        .order("kickoff_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []
