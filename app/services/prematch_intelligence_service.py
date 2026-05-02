"""Phase 6: Prematch Intelligence & Odds Movement service.

Orchestrates:
  1. Odds movement tracking (Supabase current vs DuckDB-stored opening).
  2. Availability / injury refresh via PlayerAvailabilityService.
  3. Lineup capture close to kickoff.
  4. Alert generation (drift, availability risk, lineup gaps).

Design principles:
  - Never modifies Supabase.
  - Never publishes or deletes picks.
  - All writes go to DuckDB local tables.
  - Never raises toward caller — returns stats dict.
  - Respects api_budget_service.can_run() before any API call.
  - Safe when DuckDB or Supabase are unavailable.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

# Alert type constants
ALERT_DRIFT_AGAINST  = "odds_drift_against_pick"
ALERT_DRIFT_SUPPORT  = "odds_supporting_pick"
ALERT_AVAIL_RISK     = "high_availability_risk"
ALERT_LINEUP_MISSING = "lineup_missing_favorite"
ALERT_LINEUP_NA      = "lineup_not_available"
ALERT_NO_ODDS        = "no_odds_refresh"
ALERT_STALE_ODDS     = "stale_odds"

_STRENGTH_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


# ── Main entry point ──────────────────────────────────────────────────────────


def run_prematch_intelligence(
    conn,
    hours: int = 6,
    limit: int = 50,
    execute: bool = False,
    league_filter: int | None = None,
    fixture_filter: int | None = None,
    max_requests: int | None = None,
    sync_odds: bool = True,
    sync_availability: bool = True,
    sync_lineups: bool = True,
    verbose: bool = False,
) -> dict[str, Any]:
    """Orchestrate prematch intelligence collection for upcoming fixtures.

    Args:
        conn:              DuckDB connection (already open).
        hours:             Fixture window (kickoff within next N hours).
        limit:             Max fixtures to process.
        execute:           If False: dry-run (no writes, no API calls).
        league_filter:     Restrict to one provider_league_id.
        fixture_filter:    Restrict to one provider_fixture_id.
        max_requests:      API call budget cap (overrides settings).
        sync_odds:         Whether to process odds movement.
        sync_availability: Whether to refresh injuries.
        sync_lineups:      Whether to fetch lineups when close to kickoff.
        verbose:           Print per-fixture detail.

    Returns:
        stats dict with keys: fixtures_processed, odds_rows, alerts_created,
        lineups_found, availability_updated, api_calls, errors.
    """
    stats: dict[str, Any] = {
        "fixtures_processed":   0,
        "odds_rows":            0,
        "alerts_created":       0,
        "lineups_found":        0,
        "availability_updated": 0,
        "api_calls":            0,
        "errors":               0,
    }

    budget_cap = max_requests if max_requests is not None else settings.prematch_max_requests
    request_counter = [0]

    try:
        fixtures = _collect_upcoming_fixtures(hours, limit, league_filter, fixture_filter)
    except Exception as exc:
        logger.warning("prematch: error collecting fixtures: %s", exc)
        stats["errors"] += 1
        return stats

    if verbose:
        print(f"    Fixtures encontrados: {len(fixtures)}")

    # Preload Supabase odds for all fixtures at once (no API cost)
    try:
        from app.data import supabase_reader
        fixture_ids = [f["id"] for f in fixtures if f.get("id")]
        odds_map = supabase_reader.get_odds_for_fixtures(fixture_ids) if fixture_ids else {}
    except Exception as exc:
        logger.warning("prematch: error cargando odds de Supabase: %s", exc)
        odds_map = {}

    # Preload cs_map and team_map for availability/lineups
    try:
        from app.data import supabase_reader as sr
        cs_map   = sr.get_competition_season_map()
        team_ids = list({
            t for f in fixtures
            for t in [f.get("home_team_id"), f.get("away_team_id")] if t
        })
        team_map = sr.get_team_provider_map(team_ids)
    except Exception as exc:
        logger.warning("prematch: error cargando cs/team maps: %s", exc)
        cs_map  = {}
        team_map = {}

    now = datetime.now(timezone.utc)

    for fix in fixtures:
        prov_fid = fix.get("provider_fixture_id")
        if not prov_fid:
            continue

        fid      = fix.get("id")
        league_id_sb = fix.get("league_id")
        cs_info  = cs_map.get(league_id_sb or 0, {})
        prov_lid = cs_info.get("provider_league_id")

        home_info = team_map.get(fix.get("home_team_id") or 0, {})
        away_info = team_map.get(fix.get("away_team_id") or 0, {})
        home_pid  = home_info.get("provider_team_id")
        away_pid  = away_info.get("provider_team_id")

        ko_str   = fix.get("kickoff_at") or ""
        minutes_to_ko = _minutes_to_kickoff(ko_str, now)

        if verbose:
            print(f"    fixture={prov_fid} ko_in={minutes_to_ko:.0f}min")

        fix_stats = {
            "odds": 0, "alerts": 0, "api_calls": 0,
            "avail_updated": False, "lineup_found": False,
        }

        # ── Odds movement ──────────────────────────────────────────────────────
        if sync_odds:
            try:
                fix_odds = odds_map.get(fid, [])
                _process_odds_movement(
                    conn, fix, prov_lid, fix_odds, execute, verbose, fix_stats
                )
                stats["odds_rows"] += fix_stats["odds"]
            except Exception as exc:
                logger.debug("prematch: odds error fid=%s: %s", prov_fid, exc)
                stats["errors"] += 1

        # ── Availability refresh ───────────────────────────────────────────────
        if sync_availability and home_pid and away_pid:
            if execute and request_counter[0] < budget_cap:
                try:
                    import asyncio
                    from app.services.player_availability_service import (
                        player_availability_service,
                    )
                    avail_stats = asyncio.run(
                        player_availability_service.sync_fixture(
                            conn,
                            provider_fixture_id=prov_fid,
                            home_team_id=home_pid,
                            away_team_id=away_pid,
                            fixture_id=fid,
                            league_id=prov_lid,
                            season=cs_info.get("season"),
                            sync_injuries=True,
                            sync_lineups=False,
                            dry_run=False,
                            verbose=verbose,
                        )
                    )
                    api_used = avail_stats.get("api_calls", 0)
                    request_counter[0] += api_used
                    stats["api_calls"]  += api_used
                    fix_stats["avail_updated"] = api_used > 0
                    if fix_stats["avail_updated"]:
                        stats["availability_updated"] += 1
                    _register_budget(
                        "injuries", api_used, 200, 1,
                        source_script="sync_prematch_intelligence", priority="medium",
                    )
                except Exception as exc:
                    logger.debug("prematch: avail refresh fid=%s: %s", prov_fid, exc)
                    stats["errors"] += 1

        # ── Lineup refresh (only near kickoff) ─────────────────────────────────
        if sync_lineups and minutes_to_ko <= settings.prematch_lineups_window_minutes:
            if execute and request_counter[0] < budget_cap:
                try:
                    import asyncio
                    from app.data.api_football.endpoints import fetch_lineups
                    lineups_raw = asyncio.run(fetch_lineups(prov_fid))
                    request_counter[0] += 1
                    stats["api_calls"] += 1
                    _register_budget(
                        "lineups", 1, 200, len(lineups_raw),
                        source_script="sync_prematch_intelligence", priority="medium",
                    )
                    lu_available = bool(lineups_raw)
                    fix_stats["lineup_found"] = lu_available
                    if lu_available:
                        stats["lineups_found"] += 1

                    home_lu = lineups_raw.get("home", {})
                    away_lu = lineups_raw.get("away", {})

                    if execute:
                        from app.data.local.prematch_repo import upsert_lineup_status
                        lu_row = {
                            "fixture_id":        fid,
                            "provider_fixture_id": prov_fid,
                            "home_team_id":      home_pid,
                            "away_team_id":      away_pid,
                            "home_formation":    home_lu.get("formation"),
                            "away_formation":    away_lu.get("formation"),
                            "lineups_available": lu_available,
                            "lineups_confirmed": lu_available,
                            "home_missing_count": 0,
                            "away_missing_count": 0,
                            "home_impact":       "unknown",
                            "away_impact":       "unknown",
                        }
                        # Enrich with availability if available
                        try:
                            from app.data.local.availability_repo import (
                                get_availability_for_fixture,
                            )
                            avail = get_availability_for_fixture(conn, prov_fid)
                            if avail and avail.get("summaries"):
                                s = avail["summaries"]
                                home_s = s.get(home_pid, {})
                                away_s = s.get(away_pid, {})
                                lu_row["home_missing_count"] = home_s.get("missing_count", 0)
                                lu_row["away_missing_count"] = away_s.get("missing_count", 0)
                                lu_row["home_impact"] = home_s.get("impact_label", "unknown")
                                lu_row["away_impact"] = away_s.get("impact_label", "unknown")
                        except Exception:
                            pass

                        upsert_lineup_status(conn, lu_row)

                        # Alert when lineups expected but not available
                        if not lu_available and minutes_to_ko <= 60:
                            _write_alert(conn, execute, {
                                "fixture_id":        fid,
                                "provider_fixture_id": prov_fid,
                                "league_id":         prov_lid,
                                "alert_type":        ALERT_LINEUP_NA,
                                "severity":          "medium",
                                "title":             "Alineaciones no disponibles",
                                "message": (
                                    f"Solo {minutes_to_ko:.0f} min al kickoff y las "
                                    "alineaciones aún no están publicadas."
                                ),
                                "related_market":    "",
                                "related_selection": "",
                            })
                            fix_stats["alerts"] += 1

                except Exception as exc:
                    logger.debug("prematch: lineups fid=%s: %s", prov_fid, exc)
                    stats["errors"] += 1
            elif not execute:
                # Dry-run: count lineups as 1 potential call
                stats["api_calls"] += 1

        # ── Generate alerts based on odds movement ─────────────────────────────
        try:
            n_alerts = _generate_odds_alerts(
                conn, fix, prov_lid, execute, fix_stats
            )
            fix_stats["alerts"] += n_alerts
            stats["alerts_created"] += fix_stats["alerts"]
        except Exception as exc:
            logger.debug("prematch: alerts fid=%s: %s", prov_fid, exc)
            stats["errors"] += 1

        stats["fixtures_processed"] += 1

        if request_counter[0] >= budget_cap:
            logger.info(
                "prematch: budget cap %d alcanzado — deteniendo.", budget_cap
            )
            break

    return stats


# ── Fixture collection ────────────────────────────────────────────────────────


def _collect_upcoming_fixtures(
    hours: int,
    limit: int,
    league_filter: int | None,
    fixture_filter: int | None,
) -> list[dict]:
    """Fetch upcoming fixtures from Supabase within the next N hours."""
    from app.data import supabase_reader

    if fixture_filter:
        from app.data.repositories.fixture_repo import get_fixture_by_provider_id
        fix = get_fixture_by_provider_id(fixture_filter)
        return [fix] if fix else []

    now    = datetime.now(timezone.utc)
    end    = now + timedelta(hours=hours)
    start  = now.isoformat()
    end_s  = end.isoformat()

    fixtures = supabase_reader.get_fixtures_for_range(start, end_s, limit=min(limit, 200))

    if league_filter:
        cs_map = supabase_reader.get_competition_season_map()
        fixtures = [
            f for f in fixtures
            if cs_map.get(f.get("league_id") or 0, {}).get("provider_league_id") == league_filter
        ]

    return fixtures[:limit]


# ── Odds movement processing ──────────────────────────────────────────────────


def _process_odds_movement(
    conn,
    fix: dict,
    prov_lid: int | None,
    fix_odds: list[dict],
    execute: bool,
    verbose: bool,
    fix_stats: dict,
) -> None:
    """Process odds rows for one fixture: upsert movement, track changes."""
    from app.data.local.prematch_repo import upsert_odds_movement

    prov_fid = fix["provider_fixture_id"]
    fid      = fix.get("id")
    now      = datetime.now(timezone.utc).isoformat()

    # Group by market/selection — keep best (highest) odd per market/selection
    best_by_ms: dict[tuple, dict] = {}
    all_rows: list[dict] = []

    for row in fix_odds:
        mkt = row.get("market_key") or row.get("market") or ""
        sel = row.get("selection") or ""
        odd = float(row.get("odd") or 0.0)
        bk  = row.get("bookmaker_name") or row.get("bookmaker") or ""
        if odd <= 1.0 or not mkt or not sel:
            continue
        key = (mkt, sel)
        if key not in best_by_ms or odd > best_by_ms[key]["odd"]:
            best_by_ms[key] = {"odd": odd, "bookmaker": bk}
        all_rows.append({
            "provider_fixture_id": prov_fid,
            "fixture_id":   fid,
            "league_id":    prov_lid,
            "market_key":   mkt,
            "selection":    sel,
            "bookmaker":    bk,
            "current_odds": odd,
            "best_odds":    None,
            "captured_at":  now,
            "source":       "supabase",
        })

    # Attach best_odds
    for row in all_rows:
        key = (row["market_key"], row["selection"])
        best = best_by_ms.get(key)
        if best:
            row["best_odds"] = best["odd"]

    if execute:
        for row in all_rows:
            upsert_odds_movement(conn, row)

    fix_stats["odds"] += len(all_rows)

    if verbose and all_rows:
        print(
            f"      odds: {len(all_rows)} rows"
            f" ({len(set((r['market_key'], r['selection']) for r in all_rows))} market/sel pairs)"
        )


# ── Alert generation ──────────────────────────────────────────────────────────


def _generate_odds_alerts(
    conn,
    fix: dict,
    prov_lid: int | None,
    execute: bool,
    fix_stats: dict,
) -> int:
    """Generate alerts based on odds movement stored in DuckDB. Returns alert count."""
    from app.data.local.prematch_repo import get_odds_movement_by_provider_fixture

    prov_fid = fix["provider_fixture_id"]
    fid      = fix.get("id")

    movement_rows = get_odds_movement_by_provider_fixture(conn, prov_fid)
    if not movement_rows:
        return 0

    n_alerts = 0

    # Load published picks for this fixture to check direction agreement
    recommended: set[tuple] = _get_recommended_picks(fid)

    for mv in movement_rows:
        strength  = mv.get("movement_strength", "none")
        direction = mv.get("movement_direction", "stable")
        market    = mv["market_key"]
        selection = mv["selection"]

        if strength == "none":
            continue

        is_recommended = (market, selection) in recommended

        if direction == "drifting":
            if is_recommended and _STRENGTH_ORDER.get(strength, 0) >= 2:
                severity = "high" if strength == "high" else "medium"
                n_alerts += _write_alert(conn, execute, {
                    "fixture_id":        fid,
                    "provider_fixture_id": prov_fid,
                    "league_id":         prov_lid,
                    "alert_type":        ALERT_DRIFT_AGAINST,
                    "severity":          severity,
                    "title":             f"Cuota drifting en pick recomendado ({market}/{selection})",
                    "message": (
                        f"La cuota ha subido {mv.get('odds_delta', 0):+.3f} "
                        f"(impl.delta {mv.get('implied_delta', 0):+.4f}). "
                        f"Fuerza: {strength}."
                    ),
                    "related_market":    market,
                    "related_selection": selection,
                    "metadata_json":     mv,
                })

        elif direction == "shortening":
            if is_recommended and _STRENGTH_ORDER.get(strength, 0) >= 1:
                n_alerts += _write_alert(conn, execute, {
                    "fixture_id":        fid,
                    "provider_fixture_id": prov_fid,
                    "league_id":         prov_lid,
                    "alert_type":        ALERT_DRIFT_SUPPORT,
                    "severity":          "low",
                    "title":             f"Mercado apoya pick ({market}/{selection})",
                    "message": (
                        f"La cuota ha bajado {mv.get('odds_delta', 0):+.3f} "
                        f"(fuerza {strength}). El mercado converge con el modelo."
                    ),
                    "related_market":    market,
                    "related_selection": selection,
                    "metadata_json":     mv,
                })

    return n_alerts


def _write_alert(conn, execute: bool, row: dict) -> int:
    """Write alert if execute=True. Returns 1 on success, 0 on dry-run or error."""
    if not execute:
        return 1  # count it even in dry-run (as potential)
    try:
        from app.data.local.prematch_repo import upsert_fixture_alert
        upsert_fixture_alert(conn, row)
        return 1
    except Exception as exc:
        logger.debug("prematch: write_alert failed: %s", exc)
        return 0


# ── Published picks lookup ────────────────────────────────────────────────────


def _get_recommended_picks(fixture_id: int | None) -> set[tuple]:
    """Return {(market_key, selection)} for publishable picks of this fixture."""
    if not fixture_id:
        return set()
    try:
        from app.data.repositories.supabase_client import get_supabase
        resp = (
            get_supabase()
            .table("pick_candidates")
            .select("market_key, selection")
            .eq("fixture_id", fixture_id)
            .eq("is_publishable", True)
            .execute()
        )
        return {(r["market_key"], r["selection"]) for r in (resp.data or [])}
    except Exception:
        return set()


# ── Budget hook helper ────────────────────────────────────────────────────────


def _register_budget(
    endpoint: str,
    api_calls: int,
    duration_ms: int,
    results: int,
    source_script: str,
    priority: str,
) -> None:
    if api_calls == 0:
        return
    try:
        from app.services.api_budget_service import record_call
        for _ in range(api_calls):
            record_call(
                endpoint=endpoint,
                duration_ms=duration_ms,
                status_code=200,
                results_count=results,
                source_script=source_script,
                priority=priority,
            )
    except Exception:
        pass


# ── Utility ───────────────────────────────────────────────────────────────────


def _minutes_to_kickoff(kickoff_at: str, now: datetime) -> float:
    """Return minutes between now and kickoff. Returns inf if unparseable."""
    if not kickoff_at:
        return float("inf")
    try:
        ko = datetime.fromisoformat(kickoff_at.replace("Z", "+00:00"))
        delta = (ko - now).total_seconds() / 60.0
        return delta
    except Exception:
        return float("inf")
