"""Phase 4 — Historical backfill from API-Football to local DuckDB.

Fetches fixtures, standings, and team statistics for ONE closed league/season
directly from API-Football and stores them in the local DuckDB history database.

This module does NOT write to Supabase.

Closed-season guard:
    /leagues?id=LEAGUE_ID&season=SEASON must return current=False.
    If current=True, the backfill is aborted immediately.

API call budget (per full season, no per-fixture extras):
    1  × /leagues          (coverage + current check)
    1  × /fixtures         (all season fixtures in one response)
    1  × /standings        (if coverage.standings = true)
    N  × /teams/statistics (one per unique team, typically 10-30)

Per-fixture calls (optional, use --with-per-fixture flag in CLI):
    N_fixtures × /fixtures/statistics   (if coverage.fixtures.statistics_fixtures)
    N_fixtures × /injuries              (if coverage.injuries)
    N_fixtures × /predictions           (if coverage.predictions)
    WARNING: For a 380-fixture season with all 3 enabled = ~1140 extra calls.
             The free API plan allows 100 calls/day.
             Use --limit-fixtures to cap cost during testing.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.data.api_football.client import api_client, get_rate_limit_state
from app.data.api_football.coverage import check, check_fixture_stats
from app.data.api_football.endpoints import (
    fetch_league_coverage,
    fetch_standings,
    fetch_team_statistics,
    fetch_injuries,
    fetch_provider_predictions,
)
from app.data.local.duckdb_client import get_local_db, init_schema
from app.data.local import history_repo as repo

logger = logging.getLogger(__name__)

TERMINAL_STATUSES: frozenset[str] = frozenset({"FT", "AET", "PEN", "AWD", "Canc", "WO"})


# ── Data parsers ──────────────────────────────────────────────────────────────


def _parse_fixture_row(
    item: dict, league_id: int, league_name: str, season: int
) -> dict:
    """Map a /fixtures response item to a fixtures_history row dict.

    Uses provider_fixture_id as the primary key (id) because this code path
    does not go through Supabase and has no internal Supabase ID available.
    The UNIQUE INDEX on provider_fixture_id prevents duplicates when combined
    with INSERT OR IGNORE.
    """
    fix = item.get("fixture") or {}
    teams = item.get("teams") or {}
    goals = item.get("goals") or {}
    score = item.get("score") or {}
    ht = score.get("halftime") or {}
    status = fix.get("status") or {}
    venue = fix.get("venue") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    provider_fix_id = fix.get("id")
    date_str = fix.get("date") or ""

    return {
        "id": provider_fix_id,
        "provider_fixture_id": provider_fix_id,
        "provider_league_id": league_id,
        "league_name": league_name,
        "season": season,
        "home_team_id": home.get("id"),
        "home_team_name": home.get("name"),
        "away_team_id": away.get("id"),
        "away_team_name": away.get("name"),
        "kickoff_at": date_str or None,
        "date_local": date_str[:10] if date_str else None,
        "status_short": status.get("short"),
        "goals_home": goals.get("home"),
        "goals_away": goals.get("away"),
        "halftime_home": ht.get("home"),
        "halftime_away": ht.get("away"),
        "venue_name": venue.get("name"),
    }


def _parse_standing_row(
    entry: dict,
    league_id: int,
    league_name: str,
    season: int,
    snapshot_date: str,
) -> dict:
    """Map a /standings response entry to a standings_history row dict."""
    team = entry.get("team") or {}
    all_stats = entry.get("all") or {}
    goals = all_stats.get("goals") or {}
    return {
        "provider_league_id": league_id,
        "league_name": league_name,
        "season": season,
        "team_id": team.get("id"),
        "team_name": team.get("name"),
        "rank": entry.get("rank"),
        "points": entry.get("points"),
        "played": all_stats.get("played"),
        "won": all_stats.get("win"),
        "drawn": all_stats.get("draw"),
        "lost": all_stats.get("lose"),
        "goals_for": goals.get("for"),
        "goals_against": goals.get("against"),
        "goal_diff": entry.get("goalsDiff"),
        "form": entry.get("form"),
        "snapshot_date": snapshot_date,
    }


def _parse_team_stat_row(
    stats: dict,
    team_id: int,
    team_name: str,
    league_id: int,
    season: int,
    snapshot_date: str,
) -> dict:
    """Map a /teams/statistics response to a team_stats_history row dict."""
    fixtures = stats.get("fixtures") or {}
    goals = stats.get("goals") or {}
    goals_for = goals.get("for") or {}
    goals_against = goals.get("against") or {}
    clean_sheet = stats.get("clean_sheet") or {}
    form = stats.get("form") or ""
    return {
        "provider_league_id": league_id,
        "season": season,
        "team_id": team_id,
        "team_name": team_name,
        "games_played": (fixtures.get("played") or {}).get("total"),
        "wins": (fixtures.get("wins") or {}).get("total"),
        "draws": (fixtures.get("draws") or {}).get("total"),
        "losses": (fixtures.get("loses") or {}).get("total"),  # API typo: "loses"
        "goals_for": (goals_for.get("total") or {}).get("total"),
        "goals_against": (goals_against.get("total") or {}).get("total"),
        "clean_sheets": clean_sheet.get("total"),
        "avg_goals_scored": _safe_float((goals_for.get("average") or {}).get("total")),
        "avg_goals_conceded": _safe_float(
            (goals_against.get("average") or {}).get("total")
        ),
        "form_last5": form[-5:] if form else None,
        "raw_stats": stats,
        "snapshot_date": snapshot_date,
    }


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _snapshot_date(season_end: str | None, season: int) -> str:
    """Return a consistent snapshot date for standings/team_stats rows.

    Uses the season_end from the API (e.g. '2024-05-19') when available,
    falling back to a stable synthetic date (July 31 of the season year).
    A consistent date is essential for the UNIQUE INDEX idempotency guarantee.
    """
    return season_end or f"{season}-07-31"


# ── Rate-limit helper ─────────────────────────────────────────────────────────


def _log_rate_limit() -> int | None:
    """Log current API quota and return remaining daily calls (or None if unknown)."""
    state = get_rate_limit_state()
    remaining = state.get("requests_remaining")
    limit = state.get("requests_limit")
    if remaining is not None:
        logger.info(
            "API-Football cuota diaria: %d/%d llamadas restantes",
            remaining, limit or 0,
        )
        if remaining < 20:
            logger.warning(
                "CUOTA BAJA (%d restantes) — considera usar --limit-fixtures", remaining
            )
    return remaining


# ── Main backfill entrypoint ──────────────────────────────────────────────────


async def run_backfill(
    league_id: int,
    season: int,
    *,
    dry_run: bool = False,
    limit_fixtures: int | None = None,
    with_per_fixture: bool = False,
) -> dict[str, Any]:
    """Backfill one closed league/season from API-Football to local DuckDB.

    Args:
        league_id:        API-Football provider league ID.
        season:           Four-digit season year (e.g. 2024).
        dry_run:          If True, count what would be inserted without writing.
        limit_fixtures:   Process at most this many fixtures (useful for testing).
        with_per_fixture: If True, call /fixtures/statistics, /injuries,
                          /predictions per terminal fixture. Can be expensive —
                          see module docstring for quota estimates.

    Returns:
        Summary dict: {league_id, season, fixtures_inserted, standings_rows,
                       stats_rows, injuries_fetched, predictions_fetched,
                       fixture_stats_fetched, skipped_endpoints, status}
    """
    prefix = "[DRY-RUN] " if dry_run else ""

    summary: dict[str, Any] = {
        "league_id": league_id,
        "season": season,
        "fixtures_inserted": 0,
        "standings_rows": 0,
        "stats_rows": 0,
        "injuries_fetched": 0,
        "predictions_fetched": 0,
        "fixture_stats_fetched": 0,
        "skipped_endpoints": [],
        "status": "ok",
    }

    # ── 1. Validate league/season ─────────────────────────────────────────────
    logger.info(
        "[validating_league_season] %sliga=%d season=%d", prefix, league_id, season
    )
    meta = await fetch_league_coverage(league_id, season)

    if meta is None:
        logger.error(
            "Liga/temporada no encontrada en API-Football: %d/%d — "
            "verifica que la liga exista en el plan actual",
            league_id, season,
        )
        summary["status"] = "not_found"
        return summary

    if meta.get("current", False):
        logger.warning(
            "[skipped_current_season] %sliga=%d season=%d "
            "tiene current=True en API-Football — backfill cancelado. "
            "Solo se pueden importar temporadas cerradas.",
            prefix, league_id, season,
        )
        summary["status"] = "skipped_current"
        return summary

    coverage = meta.get("coverage") or {}
    league_name = meta.get("name") or f"Liga {league_id}"
    season_end = meta.get("season_end")
    snap_date = _snapshot_date(season_end, season)

    logger.info(
        "[coverage_checked] %sliga=%d season=%d name=%r end=%s "
        "standings=%s injuries=%s predictions=%s fixture_stats=%s",
        prefix, league_id, season, league_name, season_end,
        coverage.get("standings"),
        coverage.get("injuries"),
        coverage.get("predictions"),
        (coverage.get("fixtures") or {}).get("statistics_fixtures"),
    )

    # ── 2. Fetch all fixtures for the season ──────────────────────────────────
    raw = await api_client.get(
        "/fixtures", params={"league": league_id, "season": season}
    )
    all_items: list[dict] = raw.get("response", [])
    _log_rate_limit()

    logger.info(
        "[fixtures_fetched] %sliga=%d season=%d -> %d fixtures en API",
        prefix, league_id, season, len(all_items),
    )

    if limit_fixtures is not None:
        all_items = all_items[:limit_fixtures]
        logger.info("Limitado a %d fixtures (--limit-fixtures)", limit_fixtures)

    if not all_items:
        logger.info(
            "[completed_backfill] %sliga=%d season=%d — sin fixtures, nada que insertar",
            prefix, league_id, season,
        )
        summary["status"] = "empty"
        return summary

    # ── 3. Connect to DuckDB (idempotent schema) ──────────────────────────────
    conn = get_local_db()
    init_schema(conn)

    # ── 4. Process fixtures ───────────────────────────────────────────────────
    unique_teams: dict[int, str] = {}  # team_id -> team_name (for step 5)

    for item in all_items:
        fix = item.get("fixture") or {}
        teams = item.get("teams") or {}
        status_short = (fix.get("status") or {}).get("short") or ""

        # Collect unique team IDs for team stats step
        home = teams.get("home") or {}
        away = teams.get("away") or {}
        if home.get("id"):
            unique_teams[home["id"]] = home.get("name") or ""
        if away.get("id"):
            unique_teams[away["id"]] = away.get("name") or ""

        row = _parse_fixture_row(item, league_id, league_name, season)
        if not row.get("id") or not row.get("home_team_id") or not row.get("away_team_id"):
            continue

        if not dry_run:
            repo.insert_fixture(conn, row)
        summary["fixtures_inserted"] += 1

        # Per-fixture optional calls (only for terminal fixtures)
        if with_per_fixture and status_short in TERMINAL_STATUSES:
            provider_fix_id = fix.get("id")
            await _per_fixture_calls(
                provider_fix_id, coverage, league_id, summary, dry_run, prefix
            )

    if not with_per_fixture:
        # Mark per-fixture endpoints as skipped so the summary is honest
        for ep in ("fixture_stats", "injuries", "predictions"):
            summary["skipped_endpoints"].append(ep)
        logger.debug(
            "Per-fixture calls omitidas (no se paso --with-per-fixture). "
            "Estas consumen ~3 llamadas/fixture adicionales."
        )

    # ── 5. Standings ──────────────────────────────────────────────────────────
    if check(coverage, "standings", league_id):
        if not dry_run:
            standings_entries = await fetch_standings(league_id, season)
            _log_rate_limit()
            for entry in standings_entries:
                srow = _parse_standing_row(entry, league_id, league_name, season, snap_date)
                if srow.get("team_id"):
                    repo.insert_standing(conn, srow)
                    summary["standings_rows"] += 1
        else:
            summary["standings_rows"] = -1  # unknown until real run

        logger.info(
            "[standings_saved] %sliga=%d season=%d snap=%s rows=%s",
            prefix, league_id, season, snap_date,
            summary["standings_rows"] if summary["standings_rows"] >= 0 else "N (dry-run)",
        )
    else:
        logger.info(
            "[skipped_no_coverage] standings — coverage.standings=False para liga=%d",
            league_id,
        )
        summary["skipped_endpoints"].append("standings")

    # ── 6. Team statistics (one call per unique team) ─────────────────────────
    logger.info(
        "Obteniendo estadisticas de %d equipos unicos para liga=%d season=%d",
        len(unique_teams), league_id, season,
    )
    for team_id, team_name in unique_teams.items():
        if not dry_run:
            try:
                stats = await fetch_team_statistics(team_id, league_id, season)
                if stats:
                    trow = _parse_team_stat_row(
                        stats, team_id, team_name, league_id, season, snap_date
                    )
                    repo.insert_team_stat(conn, trow)
                    summary["stats_rows"] += 1
                    logger.debug(
                        "[stats_saved] team=%d %s liga=%d season=%d",
                        team_id, team_name, league_id, season,
                    )
            except Exception as exc:
                logger.warning(
                    "team_stats fallo para team=%d liga=%d season=%d: %s",
                    team_id, league_id, season, exc,
                )
        else:
            summary["stats_rows"] += 1  # estimate = 1 per team

    if not dry_run:
        _log_rate_limit()

    if summary["stats_rows"]:
        logger.info(
            "[stats_saved] %sliga=%d season=%d -> %d equipos",
            prefix, league_id, season, summary["stats_rows"],
        )

    # ── 7. Done ───────────────────────────────────────────────────────────────
    logger.info(
        "[completed_backfill] %sliga=%d season=%d "
        "fixtures=%d standings=%s stats=%d "
        "injuries_fetched=%d predictions_fetched=%d fixture_stats_fetched=%d "
        "skipped=%s",
        prefix, league_id, season,
        summary["fixtures_inserted"],
        summary["standings_rows"] if summary["standings_rows"] >= 0 else "N",
        summary["stats_rows"],
        summary["injuries_fetched"],
        summary["predictions_fetched"],
        summary["fixture_stats_fetched"],
        summary["skipped_endpoints"],
    )
    return summary


# ── Per-fixture calls (optional, expensive) ───────────────────────────────────


async def _per_fixture_calls(
    provider_fix_id: int,
    coverage: dict,
    league_id: int,
    summary: dict,
    dry_run: bool,
    prefix: str,
) -> None:
    """Call /fixtures/statistics, /injuries, /predictions for one fixture.

    Data is counted but NOT persisted to DuckDB in this phase (no schema tables
    exist yet for match-level stats, injuries, or pre-match predictions).
    Phase 5 will add storage for these endpoints.
    """
    # /fixtures/statistics
    if check_fixture_stats(coverage, league_id):
        if not dry_run:
            try:
                data = await api_client.get(
                    "/fixtures/statistics", params={"fixture": provider_fix_id}
                )
                count = len(data.get("response", []))
                summary["fixture_stats_fetched"] += count
                logger.debug(
                    "[fixture_stats_fetched] fixture=%s -> %d team-stat blocks",
                    provider_fix_id, count,
                )
            except Exception as exc:
                logger.debug("fixture_stats fixture=%s fallo: %s", provider_fix_id, exc)
        else:
            summary["fixture_stats_fetched"] += 1
    else:
        _add_skipped(summary, "fixture_stats")

    # /injuries
    if check(coverage, "injuries", league_id, str(provider_fix_id)):
        if not dry_run:
            try:
                injuries = await fetch_injuries(provider_fix_id)
                summary["injuries_fetched"] += len(injuries)
                if injuries:
                    logger.debug(
                        "[injuries_saved] fixture=%s -> %d bajas (no persistidas)",
                        provider_fix_id, len(injuries),
                    )
            except Exception as exc:
                logger.debug("injuries fixture=%s fallo: %s", provider_fix_id, exc)
        else:
            summary["injuries_fetched"] += 1
    else:
        _add_skipped(summary, "injuries")

    # /predictions
    if check(coverage, "predictions", league_id, str(provider_fix_id)):
        if not dry_run:
            try:
                pred = await fetch_provider_predictions(provider_fix_id)
                if pred:
                    summary["predictions_fetched"] += 1
                    logger.debug(
                        "[predictions_saved] fixture=%s advice=%r (no persistida)",
                        provider_fix_id, pred.get("advice"),
                    )
            except Exception as exc:
                logger.debug("predictions fixture=%s fallo: %s", provider_fix_id, exc)
        else:
            summary["predictions_fetched"] += 1
    else:
        _add_skipped(summary, "predictions")


def _add_skipped(summary: dict, endpoint: str) -> None:
    if endpoint not in summary["skipped_endpoints"]:
        summary["skipped_endpoints"].append(endpoint)
