"""Phase 3 — Historical ingest service.

Pulls completed league/seasons from Supabase and archives them in local DuckDB.

Closed-season detection uses a DUAL SIGNAL (both must be true):
  1. competition_seasons.current = False  — API-Football marks the season non-current.
  2. >= HISTORY_MIN_TERMINAL_FRACTION of fixtures have terminal status
     (FT, AET, PEN, AWD, Canc, WO).

Rolling-window policy (per league):
  - Keep at most HISTORY_MAX_CLOSED_SEASONS closed seasons in DuckDB.
  - First-rollover exception: the first time any league would exceed that limit,
    skip pruning and mark the exception as consumed (stored in history_metadata).
  - All subsequent overflows prune the oldest season for that league.

State persistence:
  - 'first_rollover_done' key in DuckDB history_metadata table (not .env).
    This survives process restarts and is coupled to the database itself.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.data.local.duckdb_client import get_local_db, init_schema
from app.data.local import history_repo as repo
from app.data.repositories.supabase_client import get_supabase

logger = logging.getLogger(__name__)

TERMINAL_STATUSES: frozenset[str] = frozenset({"FT", "AET", "PEN", "AWD", "Canc", "WO"})
_KEY_FIRST_ROLLOVER = "first_rollover_done"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _extract_context(rows: list[dict]) -> list[dict]:
    """Merge fixture_contexts embed into each fixture row (same pattern as fixture_repo)."""
    for row in rows:
        ctx_list = row.pop("fixture_contexts", None) or []
        if isinstance(ctx_list, list) and ctx_list:
            row["context_json"] = ctx_list[0].get("context_json")
        elif isinstance(ctx_list, dict):
            row["context_json"] = ctx_list.get("context_json")
        else:
            row["context_json"] = None
    return rows


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── Signal detection ──────────────────────────────────────────────────────────


def get_eligible_closed_seasons(
    only_league: int | None = None,
    only_season: int | None = None,
) -> list[dict]:
    """Return closed seasons eligible for historical archiving.

    A season is eligible when:
      - competition_seasons.current = False
      - fraction of terminal fixtures >= settings.history_min_terminal_fraction

    Each element: {competition_season_id, provider_league_id, league_name, season,
                   season_end, total_fixtures, terminal_fraction}
    """
    client = get_supabase()

    result = (
        client.table("competition_seasons")
        .select("id, season, season_end, competitions(provider_league_id, name)")
        .eq("current", False)
        .execute()
    )

    candidates: list[dict] = []
    for row in (result.data or []):
        comp = row.get("competitions") or {}
        if not isinstance(comp, dict):
            continue
        provider_league_id = comp.get("provider_league_id")
        league_name = comp.get("name") or f"Liga {provider_league_id}"
        season = row.get("season")
        if not (provider_league_id and season):
            continue
        if only_league and provider_league_id != only_league:
            continue
        if only_season and season != only_season:
            continue
        candidates.append({
            "competition_season_id": row["id"],
            "provider_league_id": provider_league_id,
            "league_name": league_name,
            "season": season,
            "season_end": row.get("season_end"),
        })

    eligible: list[dict] = []
    for cand in candidates:
        cs_id = cand["competition_season_id"]
        fix_result = (
            client.table("fixtures")
            .select("status_short")
            .eq("league_id", cs_id)
            .execute()
        )
        fixtures = fix_result.data or []
        total = len(fixtures)

        if total == 0:
            cand["total_fixtures"] = 0
            cand["terminal_fraction"] = 1.0
            eligible.append(cand)
            continue

        terminal = sum(
            1 for f in fixtures
            if (f.get("status_short") or "") in TERMINAL_STATUSES
        )
        fraction = terminal / total
        cand["total_fixtures"] = total
        cand["terminal_fraction"] = fraction

        if fraction >= settings.history_min_terminal_fraction:
            eligible.append(cand)
        else:
            logger.debug(
                "Temporada %s/%s no elegible: terminal=%.0f/%d (%.0f%% < %.0f%%)",
                cand["provider_league_id"], season,
                terminal, total,
                fraction * 100,
                settings.history_min_terminal_fraction * 100,
            )

    return eligible


# ── Data archival ─────────────────────────────────────────────────────────────


def _archive_season_data(conn, cand: dict, client) -> dict[str, int]:
    """Fetch fixtures + picks from Supabase and insert into DuckDB.

    Returns: {fixtures, pick_results, published_picks}
    All inserts use INSERT OR IGNORE — safe to re-run.
    """
    cs_id = cand["competition_season_id"]
    provider_league_id = cand["provider_league_id"]
    league_name = cand["league_name"]
    season = cand["season"]

    # ── Fixtures ──────────────────────────────────────────────────────────────
    fix_result = (
        client.table("fixtures")
        .select(
            "id, provider_fixture_id, home_team_id, away_team_id, "
            "kickoff_at, date_local, status_short, fixture_contexts(context_json)"
        )
        .eq("league_id", cs_id)
        .execute()
    )
    fixtures = _extract_context(fix_result.data or [])

    # Resolve team names in one batch query
    team_ids: set[int] = set()
    for f in fixtures:
        if f.get("home_team_id"):
            team_ids.add(f["home_team_id"])
        if f.get("away_team_id"):
            team_ids.add(f["away_team_id"])

    teams_map: dict[int, dict] = {}
    if team_ids:
        t_result = (
            client.table("teams")
            .select("id, name, provider_team_id")
            .in_("id", list(team_ids))
            .execute()
        )
        for t in (t_result.data or []):
            teams_map[t["id"]] = t

    # Insert fixtures; build internal_id → provider_fixture_id map for picks
    internal_to_provider: dict[int, int] = {}
    fixture_count = 0
    for f in fixtures:
        ctx = f.get("context_json") or {}
        goals = ctx.get("goals") or {}
        score = ctx.get("score") or {}
        ht = score.get("halftime") or {}
        home_team = teams_map.get(f.get("home_team_id") or 0) or {}
        away_team = teams_map.get(f.get("away_team_id") or 0) or {}

        internal_to_provider[f["id"]] = f["provider_fixture_id"]
        repo.insert_fixture(conn, {
            "id": f["id"],
            "provider_fixture_id": f["provider_fixture_id"],
            "provider_league_id": provider_league_id,
            "league_name": league_name,
            "season": season,
            "home_team_id": home_team.get("provider_team_id") or f.get("home_team_id") or 0,
            "home_team_name": home_team.get("name"),
            "away_team_id": away_team.get("provider_team_id") or f.get("away_team_id") or 0,
            "away_team_name": away_team.get("name"),
            "kickoff_at": f["kickoff_at"],
            "date_local": f.get("date_local"),
            "status_short": f.get("status_short"),
            "goals_home": goals.get("home"),
            "goals_away": goals.get("away"),
            "halftime_home": ht.get("home"),
            "halftime_away": ht.get("away"),
        })
        fixture_count += 1

    if not internal_to_provider:
        return {"fixtures": 0, "pick_results": 0, "published_picks": 0}

    # ── pick_results (settled only, in chunks) ────────────────────────────────
    pick_count = 0
    CHUNK = 500
    internal_ids = list(internal_to_provider.keys())

    for i in range(0, len(internal_ids), CHUNK):
        chunk = internal_ids[i: i + CHUNK]
        pr_result = (
            client.table("pick_results")
            .select(
                "id, pick_candidate_id, fixture_id, market_key, selection, "
                "odd_taken, result_status, profit_units, settled_at"
            )
            .in_("fixture_id", chunk)
            .neq("result_status", "pending")
            .execute()
        )
        for pr in (pr_result.data or []):
            repo.insert_pick_result(conn, {
                "id": pr["id"],
                "fixture_history_id": pr["fixture_id"],
                "market_key": pr["market_key"],
                "selection": pr["selection"],
                "odd_taken": float(pr["odd_taken"]),
                "result_status": pr["result_status"],
                "settled_at": pr.get("settled_at"),
                "profit_units": _safe_float(pr.get("profit_units")),
            })
            pick_count += 1

    # ── published_picks (via pick_candidates) ─────────────────────────────────
    pp_count = 0
    for i in range(0, len(internal_ids), CHUNK):
        chunk = internal_ids[i: i + CHUNK]

        pc_result = (
            client.table("pick_candidates")
            .select(
                "id, fixture_id, market_key, selection, "
                "model_probability, implied_probability, edge, confidence_score, argument_json"
            )
            .in_("fixture_id", chunk)
            .execute()
        )
        pc_map: dict[int, dict] = {pc["id"]: pc for pc in (pc_result.data or [])}
        if not pc_map:
            continue

        pp_result = (
            client.table("published_picks")
            .select("id, pick_candidate_id, published_at")
            .in_("pick_candidate_id", list(pc_map.keys()))
            .execute()
        )
        for pp in (pp_result.data or []):
            pc = pc_map.get(pp["pick_candidate_id"]) or {}
            internal_fix_id = pc.get("fixture_id")
            provider_fix_id = internal_to_provider.get(internal_fix_id) if internal_fix_id else None
            if not provider_fix_id:
                continue
            arg_json = pc.get("argument_json") or {}
            repo.insert_published_pick(conn, {
                "id": pp["id"],
                "fixture_history_id": internal_fix_id,
                "provider_fixture_id": provider_fix_id,
                "market_key": pc.get("market_key") or "",
                "selection": pc.get("selection") or "",
                "model_probability": _safe_float(pc.get("model_probability")),
                "implied_probability": _safe_float(pc.get("implied_probability")),
                "edge": _safe_float(pc.get("edge")),
                "confidence_score": _safe_float(pc.get("confidence_score")),
                "best_odd": _safe_float(arg_json.get("best_odd")),
                "best_bookmaker": arg_json.get("best_bookmaker"),
                "published_at": pp.get("published_at"),
            })
            pp_count += 1

    return {"fixtures": fixture_count, "pick_results": pick_count, "published_picks": pp_count}


# ── Main entry point ──────────────────────────────────────────────────────────


def run_history_sync(
    *,
    dry_run: bool = False,
    only_league: int | None = None,
    only_season: int | None = None,
) -> dict[str, Any]:
    """Detect closed seasons and archive them to local DuckDB.

    Returns:
        {eligible, archived, skipped, pruned}
        Each element is a list of dicts with league/season metadata.
    """
    prefix = "[DRY-RUN] " if dry_run else ""
    conn = get_local_db()
    init_schema(conn)  # idempotent — also creates history_metadata if missing

    eligible = get_eligible_closed_seasons(only_league=only_league, only_season=only_season)

    if not eligible:
        logger.info("[nothing_to_archive] No hay temporadas cerradas elegibles")
        return {"eligible": [], "archived": [], "skipped": [], "pruned": []}

    client = get_supabase()
    first_rollover_done = (
        repo.get_metadata(conn, _KEY_FIRST_ROLLOVER, "false") == "true"
    )

    # Simulated seasons map for accurate dry-run rolling-window preview
    simulated: dict[int, list[int]] = {}

    archived: list[dict] = []
    skipped: list[dict] = []
    pruned: list[dict] = []

    for cand in eligible:
        lid = cand["provider_league_id"]
        season = cand["season"]

        # Initialise simulated state for this league on first encounter
        if lid not in simulated:
            simulated[lid] = list(repo.get_seasons_in_history(conn, lid))

        existing = simulated[lid]

        if season in existing:
            logger.debug(
                "%sTemporada %d/%d ya en histórico — omitida", prefix, lid, season
            )
            skipped.append(cand)
            continue

        logger.info(
            "[eligible_for_history] %sliga=%d season=%d "
            "fixtures=%d terminal=%.0f%%",
            prefix, lid, season,
            cand.get("total_fixtures", 0),
            cand.get("terminal_fraction", 1.0) * 100,
        )

        counts = {"fixtures": 0, "pick_results": 0, "published_picks": 0}
        if not dry_run:
            counts = _archive_season_data(conn, cand, client)

        logger.info(
            "[inserted_history] %sliga=%d season=%d "
            "fixtures=%d pick_results=%d published_picks=%d",
            prefix, lid, season,
            counts["fixtures"], counts["pick_results"], counts["published_picks"],
        )

        archived.append({**cand, **counts})

        # Update simulated state
        simulated[lid] = sorted(existing + [season])

        # ── Rolling-window prune policy ───────────────────────────────────────
        seasons_after = (
            repo.get_seasons_in_history(conn, lid) if not dry_run
            else simulated[lid]
        )

        if len(seasons_after) > settings.history_max_closed_seasons:
            if not first_rollover_done and settings.history_skip_prune_on_first_rollover:
                logger.info(
                    "[skipped_prune_first_rollover] %sliga=%d "
                    "seasons_en_historia=%d max=%d",
                    prefix, lid,
                    len(seasons_after), settings.history_max_closed_seasons,
                )
                if not dry_run:
                    repo.set_metadata(conn, _KEY_FIRST_ROLLOVER, "true")
                first_rollover_done = True  # consumed for remaining leagues in this run
            else:
                oldest = min(seasons_after)
                if not dry_run:
                    removed = repo.delete_season_from_history(conn, lid, oldest)
                    logger.info(
                        "[pruned_oldest_history] liga=%d season=%d fixtures_eliminados=%d",
                        lid, oldest, removed,
                    )
                else:
                    logger.info(
                        "[pruned_oldest_history] %sliga=%d season=%d (sin borrar)",
                        prefix, lid, oldest,
                    )
                    simulated[lid] = [s for s in simulated[lid] if s != oldest]
                pruned.append({"provider_league_id": lid, "season": oldest})

    return {
        "eligible": eligible,
        "archived": archived,
        "skipped": skipped,
        "pruned": pruned,
    }
