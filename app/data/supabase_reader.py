"""Read-only Supabase queries for the shadow value pipeline.

All functions are SELECT-only. Never writes to Supabase.
Used by audit_shadow_readiness.py and run_shadow_today.py.

Lazy-imports the Supabase client to avoid circular imports and to allow
DuckDB-only scripts to import this module without needing Supabase credentials.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Supabase pagination limit (free tier returns max 1000 rows per request)
_PAGE_SIZE = 1000


def _client():
    from app.data.repositories.supabase_client import get_supabase
    return get_supabase()


# ── Fixtures ───────────────────────────────────────────────────────────────────


def get_fixtures_for_range(
    start_utc: str,
    end_utc: str,
    limit: int = 200,
) -> list[dict]:
    """Fetch fixtures with kickoff_at between start_utc and end_utc.

    Returns list of dicts:
        id, provider_fixture_id, league_id, home_team_id, away_team_id,
        kickoff_at, status, status_short
    """
    resp = (
        _client()
        .table("fixtures")
        .select("id, provider_fixture_id, league_id, home_team_id, away_team_id, kickoff_at, status, status_short")
        .gte("kickoff_at", start_utc)
        .lt("kickoff_at", end_utc)
        .order("kickoff_at")
        .limit(min(limit, _PAGE_SIZE))
        .execute()
    )
    return resp.data or []


# ── Competition season mapping ─────────────────────────────────────────────────


def get_competition_season_map() -> dict[int, dict]:
    """Map competition_seasons.id → {provider_league_id, league_name, season}.

    Fetches all competition_seasons joined with competitions.
    Returns {cs_id: {provider_league_id, league_name, season}}.
    """
    resp = (
        _client()
        .table("competition_seasons")
        .select("id, season, competitions(provider_league_id, name)")
        .limit(_PAGE_SIZE)
        .execute()
    )
    result: dict[int, dict] = {}
    for row in resp.data or []:
        comp = row.get("competitions") or {}
        result[row["id"]] = {
            "provider_league_id": comp.get("provider_league_id"),
            "league_name":        comp.get("name") or "",
            "season":             row.get("season"),
        }
    return result


# ── Team provider mapping ──────────────────────────────────────────────────────


def get_team_provider_map(team_ids: list[int]) -> dict[int, dict]:
    """Map teams.id → {provider_team_id, name}.

    Returns {team_id: {provider_team_id, name}}.
    Fetches in batches to avoid URL length limits.
    """
    if not team_ids:
        return {}

    result: dict[int, dict] = {}
    batch_size = 200
    for i in range(0, len(team_ids), batch_size):
        batch = team_ids[i : i + batch_size]
        resp = (
            _client()
            .table("teams")
            .select("id, provider_team_id, name")
            .in_("id", batch)
            .limit(_PAGE_SIZE)
            .execute()
        )
        for r in resp.data or []:
            result[r["id"]] = {
                "provider_team_id": r["provider_team_id"],
                "name":             r.get("name") or "",
            }
    return result


# ── Odds snapshots ─────────────────────────────────────────────────────────────


def get_odds_for_fixtures(fixture_ids: list[int]) -> dict[int, list[dict]]:
    """Fetch best available prematch odds per fixture.

    Returns {fixture_id: [{market_key, selection, odd, bookmaker_name}]}.
    Groups all rows by fixture_id for easy per-fixture lookup.
    Fetches in batches to stay within Supabase query limits.
    """
    if not fixture_ids:
        return {}

    all_rows: list[dict] = []
    batch_size = 50
    for i in range(0, len(fixture_ids), batch_size):
        batch = fixture_ids[i : i + batch_size]
        resp = (
            _client()
            .table("odds_snapshots")
            .select("fixture_id, market_key, selection, odd, bookmaker_name")
            .in_("fixture_id", batch)
            .eq("scope", "prematch")
            .limit(_PAGE_SIZE)
            .execute()
        )
        all_rows.extend(resp.data or [])

    result: dict[int, list[dict]] = {}
    for r in all_rows:
        fid = r["fixture_id"]
        result.setdefault(fid, []).append({
            "market_key":     r["market_key"],
            "selection":      r["selection"],
            "odd":            float(r["odd"]),
            "bookmaker_name": r.get("bookmaker_name") or "",
        })
    return result


# ── DuckDB readiness checks (read from DuckDB, not Supabase) ───────────────────


def check_duckdb_readiness(conn) -> dict:
    """Return DuckDB local inventory needed for fixture readiness checks.

    Returns dict with:
        counts per table, set of league_ids with training_samples,
        set of league_ids with specific calibrators, has_global_calibrator bool.
    """
    def _count(table: str) -> int:
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except Exception:
            return -1

    leagues_with_samples = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT provider_league_id FROM training_samples"
        ).fetchall()
    }
    leagues_with_calibrators = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT provider_league_id FROM calibration_registry "
            "WHERE provider_league_id IS NOT NULL"
        ).fetchall()
    }
    global_entity_types = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT entity_type FROM calibration_registry "
            "WHERE provider_league_id IS NULL"
        ).fetchall()
    }

    return {
        "fixtures_history":       _count("fixtures_history"),
        "training_samples":       _count("training_samples"),
        "team_elo_history":       _count("team_elo_history"),
        "calibration_registry":   _count("calibration_registry"),
        "market_quality_summary": _count("market_quality_summary"),
        "shadow_value_picks":     _count("shadow_value_picks"),
        "leagues_with_samples":         leagues_with_samples,
        "leagues_with_calibrators":     leagues_with_calibrators,
        "global_calibrator_scopes":     global_entity_types,
        "has_global_calibrator":        bool(global_entity_types),
    }
