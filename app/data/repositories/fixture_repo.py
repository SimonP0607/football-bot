"""Supabase repository for competitions, competition_seasons, teams, and fixtures.

Schema (migration 010):
  Layer A (catalog): competitions, competition_seasons, tracked_competitions, teams, venues
  Layer B (hot):     fixtures, fixture_contexts

Public API mirrors the old league-centric API so callers (sync_service, bot handlers)
require minimal changes:
  - upsert_league()       → wraps upsert_competition() + upsert_competition_season()
                            returns {"id": competition_season_id, ...}
  - get_league_coverage() → reads competition_seasons.coverage
  - get_active_league_seasons() → reads competition_seasons WHERE current=True
  - get_active_leagues()  → reads competition_seasons JOIN competitions WHERE is_active=True
  - get_leagues_by_ids()  → returns {competition_season_id: name} for /hoy and /partido
  - upsert_fixture()      → league_id param → stored in fixtures.league_id FK → competition_seasons.id
  - update_fixture_context() → upserts into fixture_contexts (separate table)
  - get_fixtures_today(), get_fixture_by_internal_id(), get_fixture_by_provider_id()
                          → include context_json merged from fixture_contexts
"""

import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.data.repositories.supabase_client import get_supabase
from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _flatten_context(rows: list[dict]) -> list[dict]:
    """Merge fixture_contexts embed into each fixture row as context_json.

    PostgREST returns fixture_contexts as a list (one-to-many FK direction).
    Since fixture_contexts.fixture_id is UNIQUE, there is at most one entry.
    """
    for row in rows:
        ctx = row.pop("fixture_contexts", None) or []
        if isinstance(ctx, list) and ctx:
            row["context_json"] = ctx[0].get("context_json")
        elif isinstance(ctx, dict):
            row["context_json"] = ctx.get("context_json")
        else:
            row["context_json"] = None
    return rows


# ── Competitions (persistent catalog) ────────────────────────────────────────


def upsert_competition(
    provider_league_id: int,
    name: str,
    country: str | None,
    league_type: str | None = None,
) -> dict:
    """Insert or update a competition row. Returns the stored row."""
    client = get_supabase()
    payload: dict = {
        "provider_league_id": provider_league_id,
        "name": name,
        "country": country,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    if league_type:
        payload["type"] = league_type

    result = (
        client.table("competitions")
        .upsert(payload, on_conflict="provider_league_id")
        .execute()
    )
    row = result.data[0] if result.data else {}
    logger.debug(
        "upsert_competition provider_league_id=%s → id=%s",
        provider_league_id, row.get("id"),
    )
    return row


def upsert_competition_season(
    competition_id: int,
    season: int,
    *,
    coverage: dict | None = None,
    season_start: str | None = None,
    season_end: str | None = None,
    current: bool = False,
) -> dict:
    """Insert or update a competition_season row. Returns the stored row."""
    client = get_supabase()
    payload: dict = {
        "competition_id": competition_id,
        "season": season,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    if coverage is not None:
        payload["coverage"] = coverage
    if season_start:
        payload["season_start"] = season_start
    if season_end:
        payload["season_end"] = season_end
    if current:
        payload["current"] = True
        payload["is_active"] = True

    result = (
        client.table("competition_seasons")
        .upsert(payload, on_conflict="competition_id,season")
        .execute()
    )
    row = result.data[0] if result.data else {}
    logger.debug(
        "upsert_competition_season competition_id=%s season=%s → id=%s",
        competition_id, season, row.get("id"),
    )
    return row


# ── League-compatible wrapper (used by sync_service) ─────────────────────────


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
    """Insert or update a competition + competition_season.

    Wraps upsert_competition() and upsert_competition_season() so that
    sync_service.py requires no changes.

    Returns a dict where ``id`` is the competition_season internal ID.
    This ID is used as the FK in fixtures.league_id.
    """
    comp_row = upsert_competition(
        provider_league_id=provider_league_id,
        name=name,
        country=country,
        league_type=league_type,
    )
    competition_id = comp_row.get("id")
    if not competition_id:
        logger.warning(
            "upsert_league: no competition_id para provider_league_id=%s", provider_league_id
        )
        return {}

    cs_row = upsert_competition_season(
        competition_id=competition_id,
        season=season,
        coverage=coverage,
        season_start=season_start,
        season_end=season_end,
        current=current,
    )
    return cs_row


def get_league_coverage(provider_league_id: int, season: int) -> dict | None:
    """Return coverage jsonb for a league+season from competition_seasons.

    Returns:
        dict  — coverage object (may be empty {}).
        None  — the competition_season row does not exist (Phase B not run).
    """
    client = get_supabase()
    comp_result = (
        client.table("competitions")
        .select("id")
        .eq("provider_league_id", provider_league_id)
        .limit(1)
        .execute()
    )
    if not comp_result.data:
        return None
    competition_id = comp_result.data[0]["id"]

    cs_result = (
        client.table("competition_seasons")
        .select("coverage")
        .eq("competition_id", competition_id)
        .eq("season", season)
        .limit(1)
        .execute()
    )
    if cs_result.data:
        return cs_result.data[0].get("coverage") or {}
    return None


def get_competition_season_by_id(competition_season_id: int) -> dict | None:
    """Return {provider_league_id, season, coverage} for a competition_season.

    Used by Phase D (sync_prematch) to resolve league metadata from the
    fixtures.league_id FK value without directly querying the old leagues table.
    """
    client = get_supabase()
    result = (
        client.table("competition_seasons")
        .select("season, coverage, competitions(provider_league_id)")
        .eq("id", competition_season_id)
        .limit(1)
        .execute()
    )
    if result.data:
        row = result.data[0]
        comp = row.get("competitions") or {}
        return {
            "provider_league_id": comp.get("provider_league_id"),
            "season": row.get("season"),
            "coverage": row.get("coverage") or {},
        }
    return None


def get_tracked_league_seasons() -> dict[int, dict]:
    """Return {provider_league_id: {season, sync_tier, competition_season_id}}
    from tracked_competitions WHERE is_active=True.

    This is the primary source for Phase C (sync_daily). It reads which leagues
    the operator wants to track and at what sync tier, avoiding the need to
    use DEFAULT_LEAGUE_IDS for Phase C decisions.

    Returns empty dict if tracked_competitions has no active rows (fall back to
    get_active_league_seasons in that case).
    """
    client = get_supabase()
    try:
        result = (
            client.table("tracked_competitions")
            .select(
                "competition_season_id, sync_tier, "
                "competition_seasons(season, competitions(provider_league_id))"
            )
            .eq("is_active", True)
            .execute()
        )
        out: dict[int, dict] = {}
        for row in (result.data or []):
            cs = row.get("competition_seasons")
            if not cs or not isinstance(cs, dict):
                continue
            comp = cs.get("competitions")
            if not comp or not isinstance(comp, dict):
                continue
            lid = comp.get("provider_league_id")
            season = cs.get("season")
            if lid and season:
                out[lid] = {
                    "season": season,
                    "sync_tier": row.get("sync_tier") or "tier_1_daily",
                    "competition_season_id": row.get("competition_season_id"),
                }
        logger.debug("get_tracked_league_seasons: %d ligas activas", len(out))
        return out
    except Exception as exc:
        logger.warning("get_tracked_league_seasons falló: %s", exc)
        return {}


def get_active_league_seasons(league_ids: list[int] | None = None) -> dict[int, int]:
    """Return {provider_league_id: season} for seasons with current=True.

    Populated during Phase B. Used by Phase C to auto-detect which
    leagues/seasons to sync without explicit configuration.

    Args:
        league_ids: If provided, only include leagues in this list.

    Returns:
        Dict mapping provider_league_id → season. Empty if none found.
    """
    client = get_supabase()
    try:
        result = (
            client.table("competition_seasons")
            .select("season, competitions(provider_league_id)")
            .eq("current", True)
            .order("season", desc=True)
            .execute()
        )

        mapping: dict[int, int] = {}
        for row in (result.data or []):
            comp = row.get("competitions") or {}
            lid = comp.get("provider_league_id") if isinstance(comp, dict) else None
            season = row.get("season")
            if lid and season and lid not in mapping:
                if league_ids and lid not in league_ids:
                    continue
                mapping[lid] = season

        logger.debug("Ligas activas en BD (current=true): %s", mapping)
        return mapping
    except Exception as exc:
        logger.warning("get_active_league_seasons falló: %s", exc)
        return {}


def get_active_leagues() -> list[dict]:
    """Return all active league-seasons with sync_tier, ordered by name."""
    client = get_supabase()
    # Get competition_seasons that are active
    cs_result = (
        client.table("competition_seasons")
        .select("id, season, coverage, competitions(provider_league_id, name, country)")
        .eq("is_active", True)
        .execute()
    )
    # Get tracked_competitions for sync_tier info
    tc_result = (
        client.table("tracked_competitions")
        .select("competition_season_id, sync_tier, priority")
        .eq("is_active", True)
        .execute()
    )
    tc_by_cs: dict[int, dict] = {
        row["competition_season_id"]: row for row in (tc_result.data or [])
    }

    leagues = []
    for row in (cs_result.data or []):
        comp = row.get("competitions") or {}
        cs_id = row["id"]
        tc = tc_by_cs.get(cs_id, {})
        leagues.append({
            "id": cs_id,
            "provider_league_id": comp.get("provider_league_id"),
            "name": comp.get("name", ""),
            "country": comp.get("country"),
            "season": row.get("season"),
            "coverage": row.get("coverage"),
            "sync_tier": tc.get("sync_tier", "tier_1_daily"),
            "priority": tc.get("priority", 5),
        })
    return sorted(leagues, key=lambda x: (x.get("priority") or 5, x.get("name") or ""))


def get_leagues_by_ids(league_ids: set[int]) -> dict[int, str]:
    """Return {competition_season_id: competition_name} for a set of season IDs.

    league_ids here are competition_season internal IDs (fixtures.league_id FK).
    """
    if not league_ids:
        return {}
    client = get_supabase()
    result = (
        client.table("competition_seasons")
        .select("id, competitions(name)")
        .in_("id", list(league_ids))
        .execute()
    )
    out: dict[int, str] = {}
    for row in (result.data or []):
        comp = row.get("competitions") or {}
        name = comp.get("name") if isinstance(comp, dict) else None
        out[row["id"]] = name or ""
    return out


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
    """Insert or update a fixture. Returns the stored row.

    ``league_id`` is the internal competition_season ID (fixtures.league_id FK
    → competition_seasons.id). The column keeps the name ``league_id`` so that
    sync_service.py requires no changes.
    """
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
    """Upsert the match context blob into fixture_contexts (separate table)."""
    client = get_supabase()
    try:
        client.table("fixture_contexts").upsert(
            {
                "fixture_id": fixture_id,
                "context_json": context_json,
                "updated_at": datetime.utcnow().isoformat() + "Z",
            },
            on_conflict="fixture_id",
        ).execute()
        logger.debug("fixture_contexts upserted para fixture_id=%s", fixture_id)
    except Exception as exc:
        logger.warning(
            "No se pudo guardar context_json para fixture_id=%s: %s", fixture_id, exc
        )


def get_fixtures_today() -> list[dict]:
    """Return all fixtures whose kickoff is within the current local calendar day.

    Includes context_json merged from fixture_contexts.
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
        .select("*, fixture_contexts(context_json)")
        .gte("kickoff_at", day_start)
        .lte("kickoff_at", day_end)
        .order("kickoff_at")
        .execute()
    )
    return _flatten_context(result.data or [])


def get_fixture_by_internal_id(fixture_id: int) -> dict | None:
    """Return a single fixture by its internal (Supabase) ID, or None.

    Includes context_json merged from fixture_contexts.
    """
    client = get_supabase()
    result = (
        client.table("fixtures")
        .select("*, fixture_contexts(context_json)")
        .eq("id", fixture_id)
        .limit(1)
        .execute()
    )
    rows = _flatten_context(result.data or [])
    return rows[0] if rows else None


def get_fixture_by_provider_id(provider_fixture_id: int) -> dict | None:
    """Return a fixture by its API-Football provider ID, or None.

    Includes context_json merged from fixture_contexts.
    """
    client = get_supabase()
    result = (
        client.table("fixtures")
        .select("*, fixture_contexts(context_json)")
        .eq("provider_fixture_id", provider_fixture_id)
        .limit(1)
        .execute()
    )
    rows = _flatten_context(result.data or [])
    return rows[0] if rows else None


def search_fixtures_by_team_name(query: str, limit: int = 5) -> list[dict]:
    """Search today's fixtures containing a team whose name matches the query.

    Includes context_json merged from fixture_contexts.
    """
    client = get_supabase()
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
        .select("*, fixture_contexts(context_json)")
        .gte("kickoff_at", day_start)
        .lte("kickoff_at", day_end)
        .in_("home_team_id", team_ids)
        .order("kickoff_at")
        .limit(limit)
        .execute()
    )
    result2 = (
        client.table("fixtures")
        .select("*, fixture_contexts(context_json)")
        .gte("kickoff_at", day_start)
        .lte("kickoff_at", day_end)
        .in_("away_team_id", team_ids)
        .order("kickoff_at")
        .limit(limit)
        .execute()
    )
    seen: set[int] = set()
    combined: list[dict] = []
    for row in (result.data or []) + (result2.data or []):
        if row["id"] not in seen:
            seen.add(row["id"])
            combined.append(row)
    return _flatten_context(combined[:limit])


def get_h2h_fixtures(
    home_team_id: int,
    away_team_id: int,
    limit: int = 5,
) -> list[dict]:
    """Return recent head-to-head fixtures between two teams (any order)."""
    client = get_supabase()
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
