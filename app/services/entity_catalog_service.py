"""Entity catalog service — sync teams, squads, and players from API-Football.

Responsible for:
  - Reading active leagues from tracked_competitions (or explicit override).
  - Fetching teams per league/season via /teams.
  - Persisting team_identity and team_season_membership in DuckDB.
  - Optionally fetching basic squad via /players/squads (cheap: 1 call per team).
  - Respecting the API budget: blocks low-priority phases when quota is low.
  - Never modifying picks, Value Engine, or the Supabase fixtures pipeline.

Tier policy:
  tier_1_daily   — teams + squads (when include_squads=True)
  tier_2_matchday — teams + squads only with include_squads flag
  tier_3_light   — teams only (no squads ever)
  national teams — squads allowed when include_squads=True

Does NOT:
  - Call /players (paginado masivo) by default.
  - Modify any Supabase table (all writes go to DuckDB).
  - Change picks, predictions, or the Value Engine.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class EntityCatalogService:
    """Orchestrates entity catalog sync from API-Football into DuckDB."""

    # ── Public interface ──────────────────────────────────────────────────────

    async def sync_for_league(
        self,
        conn,  # duckdb.DuckDBPyConnection
        provider_league_id: int,
        season: int,
        *,
        sync_tier: str = "tier_1_daily",
        competition_type: str | None = None,
        scope: str = "club",
        country: str | None = None,
        include_squads: bool = False,
        only_teams: bool = False,
        only_squads: bool = False,
        dry_run: bool = True,
        max_requests: int | None = None,
        request_counter: list[int] | None = None,
        verbose: bool = False,
    ) -> dict:
        """Sync teams (and optionally squads) for one league/season.

        Returns:
            {teams_found, teams_written, memberships_written,
             squads_written, players_written, api_calls, errors}
        """
        from app.services.api_budget_service import can_run
        from app.data.api_football.endpoints import fetch_teams, fetch_players_squad
        from app.data.local.entity_repo import (
            upsert_team_identity,
            upsert_team_membership,
            upsert_player_identity,
            upsert_squad_membership,
        )

        rc = request_counter if request_counter is not None else [0]
        stats: dict[str, int] = {
            "teams_found": 0, "teams_written": 0,
            "memberships_written": 0, "squads_written": 0,
            "players_written": 0, "api_calls": 0, "errors": 0,
        }

        # Dry-run: report plan without any API calls
        if dry_run:
            stats["teams_written"] = 1       # indicate "would run"
            stats["memberships_written"] = 1
            return stats

        # Budget gate
        if not can_run("medium", "entity_catalog_service"):
            logger.info(
                "Budget gate: skipping league=%s season=%s (medium priority blocked)",
                provider_league_id, season,
            )
            return stats

        if max_requests and rc[0] >= max_requests:
            logger.info("max_requests reached — skipping league=%s", provider_league_id)
            return stats

        # -- Fetch teams -------------------------------------------------------
        if not only_squads:
            try:
                teams = await fetch_teams(provider_league_id, season)
                rc[0] += 1
                stats["api_calls"] += 1
                stats["teams_found"] = len(teams)
            except Exception as exc:
                logger.warning(
                    "fetch_teams failed league=%s season=%s: %s", provider_league_id, season, exc
                )
                stats["errors"] += 1
                return stats

            for team in teams:
                tid = team.get("provider_team_id")
                if not tid:
                    continue

                team_scope = "national_team" if team.get("is_national") else scope

                if verbose:
                    print(
                        f"    {'[DRY]' if dry_run else ''} team={tid} "
                        f"{team['name']} ({team.get('country', '?')}) "
                        f"national={team.get('is_national')}"
                    )

                if not dry_run:
                    upsert_team_identity(conn, team)
                    upsert_team_membership(
                        conn,
                        provider_team_id=tid,
                        provider_league_id=provider_league_id,
                        season=season,
                        competition_type=competition_type,
                        scope=team_scope,
                        country=country or team.get("country"),
                        source="api",
                    )
                    stats["teams_written"] += 1
                    stats["memberships_written"] += 1
                else:
                    stats["teams_written"] += 1
                    stats["memberships_written"] += 1

        # -- Fetch squads (optional) -------------------------------------------
        if include_squads and not only_teams:
            if sync_tier == "tier_3_light":
                logger.debug(
                    "tier_3_light: skipping squads for league=%s", provider_league_id
                )
            else:
                if not can_run("medium", "entity_catalog_squads"):
                    logger.info("Budget gate: squads skipped (medium priority blocked)")
                    return stats

                # Collect team IDs from this league
                if only_squads:
                    try:
                        teams = await fetch_teams(provider_league_id, season)
                        rc[0] += 1
                        stats["api_calls"] += 1
                    except Exception as exc:
                        logger.warning("fetch_teams (squads-only mode) failed: %s", exc)
                        stats["errors"] += 1
                        return stats

                for team in teams:
                    tid = team.get("provider_team_id")
                    if not tid:
                        continue
                    if max_requests and rc[0] >= max_requests:
                        logger.info("max_requests reached — stopping squads")
                        break

                    if verbose:
                        print(
                            f"    {'[DRY]' if dry_run else ''} squad team={tid} "
                            f"{team['name']} ..."
                        )

                    try:
                        players = await fetch_players_squad(tid)
                        rc[0] += 1
                        stats["api_calls"] += 1
                    except Exception as exc:
                        logger.warning("fetch_players_squad team=%s failed: %s", tid, exc)
                        stats["errors"] += 1
                        continue

                    for player in players:
                        pid = player.get("provider_player_id")
                        if not pid:
                            continue
                        if not dry_run:
                            # Minimal player identity from squad endpoint
                            upsert_player_identity(conn, {
                                "provider_player_id": pid,
                                "name": player.get("name", ""),
                                "age": player.get("age"),
                                "photo": player.get("photo"),
                            })
                            upsert_squad_membership(
                                conn,
                                provider_player_id=pid,
                                provider_team_id=tid,
                                season=season,
                                provider_league_id=provider_league_id,
                                position=player.get("position"),
                                number=player.get("number"),
                                source="squads_endpoint",
                            )
                            stats["players_written"] += 1
                            stats["squads_written"] += 1
                        else:
                            stats["players_written"] += 1
                            stats["squads_written"] += 1

        return stats

    async def sync_all_leagues(
        self,
        conn,  # duckdb.DuckDBPyConnection
        *,
        league_filter: int | None = None,
        tier_filter: int | None = None,
        season_override: int | None = None,
        include_squads: bool = False,
        only_teams: bool = False,
        only_squads: bool = False,
        national_only: bool = False,
        club_only: bool = False,
        dry_run: bool = True,
        max_requests: int | None = None,
        limit: int | None = None,
        verbose: bool = False,
    ) -> dict:
        """Sync all active tracked leagues.

        Returns aggregate stats + per-league breakdown.
        """
        from app.data.repositories.fixture_repo import get_tracked_league_seasons

        tracked = get_tracked_league_seasons()

        if not tracked:
            logger.warning("No tracked leagues found — run sync_reference first")
            return {"error": "no_tracked_leagues", "leagues": 0}

        # Filters
        if league_filter:
            tracked = {lid: v for lid, v in tracked.items() if lid == league_filter}
        if tier_filter:
            tier_str = f"tier_{tier_filter}_"
            tracked = {
                lid: v for lid, v in tracked.items()
                if (v.get("sync_tier") or "").startswith(tier_str)
            }

        # Apply limit
        if limit:
            items = list(tracked.items())[:limit]
            tracked = dict(items)

        total_stats: dict[str, int] = {
            "leagues_requested": len(tracked),
            "teams_found": 0, "teams_written": 0,
            "memberships_written": 0, "squads_written": 0,
            "players_written": 0, "api_calls": 0, "errors": 0,
        }
        request_counter = [0]
        per_league: list[dict] = []

        for provider_league_id, meta in tracked.items():
            season = season_override or meta.get("season")
            if not season:
                logger.warning("No season for league=%s — skipping", provider_league_id)
                continue

            sync_tier = meta.get("sync_tier") or "tier_1_daily"

            # Determine entity scope from competition_context if available
            scope, competition_type = _resolve_scope(conn, provider_league_id)

            if national_only and scope != "national_team":
                continue
            if club_only and scope == "national_team":
                continue

            print(
                f"  league={provider_league_id} season={season} tier={sync_tier} "
                f"scope={scope}"
                + (" [DRY-RUN]" if dry_run else ""),
                flush=True,
            )

            league_stats = await self.sync_for_league(
                conn,
                provider_league_id=provider_league_id,
                season=season,
                sync_tier=sync_tier,
                competition_type=competition_type,
                scope=scope,
                include_squads=include_squads,
                only_teams=only_teams,
                only_squads=only_squads,
                dry_run=dry_run,
                max_requests=max_requests,
                request_counter=request_counter,
                verbose=verbose,
            )

            for k in ("teams_found", "teams_written", "memberships_written",
                      "squads_written", "players_written", "api_calls", "errors"):
                total_stats[k] = total_stats.get(k, 0) + league_stats.get(k, 0)

            per_league.append({
                "provider_league_id": provider_league_id,
                "season": season,
                "sync_tier": sync_tier,
                "scope": scope,
                **league_stats,
            })

            if max_requests and request_counter[0] >= max_requests:
                print(f"  max_requests={max_requests} reached — stopping.")
                break

        total_stats["per_league"] = per_league  # type: ignore[assignment]
        return total_stats


# ── Module-level helpers ──────────────────────────────────────────────────────


def _resolve_scope(conn, provider_league_id: int) -> tuple[str, str | None]:
    """Return (scope, competition_type) from competition_context if available.

    Falls back to ('club', None) when the league is not in competition_context.
    """
    try:
        row = conn.execute(
            "SELECT entity_scope, competition_type FROM competition_context WHERE provider_league_id = ?",
            [provider_league_id],
        ).fetchone()
        if row:
            return row[0] or "club", row[1]
    except Exception:
        pass
    return "club", None


entity_catalog_service = EntityCatalogService()
