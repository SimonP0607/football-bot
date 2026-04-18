"""Sync service: multi-phase data ingestion from API-Football into Supabase.

Phase A  reference sync   — bookmakers, bet types, timezone validation
Phase B  bootstrap sync   — league coverage metadata (run once per season)
Phase C  daily sync       — fixtures + context + odds (coverage-first)
Phase D  prematch sync    — lineups + odds refresh near kickoff

Coverage-first principle:
  Before calling standings, injuries, predictions, or odds, the service reads
  the stored leagues.coverage. If a flag is false the call is skipped and a
  debug trace is logged so every omission is auditable.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.data.api_football import endpoints, coverage as cov_helper
from app.data.repositories import fixture_repo, odds_repo
from app.data.repositories import reference_repo, sync_runs_repo
from app.data.repositories.supabase_client import get_supabase

logger = logging.getLogger(__name__)


class SyncService:
    """Orchestrates all sync phases for API-Football data ingestion."""

    # ── Phase A: Reference data ───────────────────────────────────────────────

    async def sync_reference(self) -> dict:
        """Phase A: populate ref_bookmakers and ref_bet_types, validate timezone.

        Safe to run repeatedly — all upserts are idempotent.
        This phase does NOT depend on any specific league or season.
        """
        run_id = sync_runs_repo.start_sync_run("reference")
        api_calls = 0
        try:
            # Validate configured timezone
            tz = settings.default_timezone
            valid = await endpoints.validate_timezone(tz)
            api_calls += 1
            if not valid:
                logger.warning("Timezone '%s' no válida para API-Football — revisa DEFAULT_TIMEZONE", tz)

            # Bookmakers
            bookmakers = await endpoints.fetch_bookmakers()
            api_calls += 1
            n_bk = reference_repo.upsert_bookmakers(bookmakers)

            # Bet types (prematch only — never mix with live bet IDs)
            bet_types = await endpoints.fetch_bet_types()
            api_calls += 1
            n_bt = reference_repo.upsert_bet_types(bet_types, scope="prematch")

            summary = {
                "timezone_valid": valid,
                "bookmakers_saved": n_bk,
                "bet_types_saved": n_bt,
            }
            logger.info("Phase A completada: %s", summary)

            self._save_usage_snapshot(run_id, "/odds/bookmakers")
            sync_runs_repo.finish_sync_run(
                run_id,
                status="completed",
                api_calls_made=api_calls,
                summary_json=summary,
            )
            return summary

        except Exception as exc:
            logger.exception("Phase A falló: %s", exc)
            sync_runs_repo.finish_sync_run(
                run_id, status="failed", error_message=str(exc), api_calls_made=api_calls
            )
            raise

    # ── Phase B: League coverage bootstrap ───────────────────────────────────

    async def sync_bootstrap(self, league_seasons: dict[int, int]) -> dict:
        """Phase B: fetch league metadata + coverage from /leagues.

        Stores coverage in leagues.coverage so Phase C can skip unavailable endpoints.
        Run once per season, or whenever you add a new league.
        """
        run_id = sync_runs_repo.start_sync_run("bootstrap")
        api_calls = 0
        updated = 0
        try:
            for league_id, season in league_seasons.items():
                meta = await endpoints.fetch_league_coverage(league_id, season)
                api_calls += 1
                if not meta:
                    logger.warning(
                        "Phase B: no hay datos para league=%s season=%s", league_id, season
                    )
                    continue

                fixture_repo.upsert_league(
                    provider_league_id=league_id,
                    name=meta.get("name", str(league_id)),
                    country=meta.get("country"),
                    season=season,
                    coverage=meta.get("coverage"),
                    season_start=meta.get("season_start"),
                    season_end=meta.get("season_end"),
                    current=meta.get("current", False),
                    league_type=meta.get("type"),
                )
                updated += 1
                logger.info(
                    "Phase B: league=%s season=%s coverage guardada (standings=%s, injuries=%s, predictions=%s)",
                    league_id, season,
                    meta.get("coverage", {}).get("standings"),
                    meta.get("coverage", {}).get("injuries"),
                    meta.get("coverage", {}).get("predictions"),
                )

            summary = {"leagues_updated": updated}
            sync_runs_repo.finish_sync_run(
                run_id,
                status="completed",
                leagues_synced=updated,
                api_calls_made=api_calls,
                summary_json=summary,
            )
            return summary

        except Exception as exc:
            logger.exception("Phase B falló: %s", exc)
            sync_runs_repo.finish_sync_run(
                run_id, status="failed", error_message=str(exc), api_calls_made=api_calls
            )
            raise

    # ── Phase C: Daily operational sync ──────────────────────────────────────

    async def sync_daily(
        self,
        league_seasons: dict[int, int],
        timezone: str | None = None,
    ) -> dict:
        """Phase C: sync today's fixtures + context + odds.

        For each league:
          1. Read coverage from DB.
          2. Fetch standings once (coverage-first).
          3. Fetch fixtures for today.
          4. Per fixture: upsert league/team/fixture, build context blob, fetch odds.

        Args:
            league_seasons: {league_id: season_year}
            timezone: IANA timezone override (defaults to settings.default_timezone).

        Returns:
            Summary dict with counts by league.
        """
        tz_name = timezone or settings.default_timezone
        tz = ZoneInfo(tz_name)
        today_str = datetime.now(tz).strftime("%Y-%m-%d")
        markets = settings.markets_list
        preferred_bk_id = settings.preferred_bookmaker_id or None

        run_id = sync_runs_repo.start_sync_run("daily")
        api_calls = 0
        total_fixtures = 0
        total_odds = 0

        logger.info(
            "=== Phase C — Sync diario | fecha=%s | timezone=%s | ligas=%s ===",
            today_str, tz_name, list(league_seasons.keys()),
        )

        league_results: dict[int, dict] = {}

        try:
            for league_id, season in league_seasons.items():
                # Read coverage from DB (populated during Phase B)
                coverage = fixture_repo.get_league_coverage(league_id, season)

                # Fetch standings once per league (saves API calls)
                standings_data: list[dict] = []
                if cov_helper.check(coverage, "standings", league_id):
                    try:
                        standings_data = await endpoints.fetch_standings(league_id, season)
                        api_calls += 1
                    except Exception:
                        logger.warning("standings falló para league=%s — continuando sin él", league_id)
                else:
                    logger.debug(
                        "coverage.standings=false para league=%s season=%s — omitido", league_id, season
                    )

                # Fetch fixtures for today
                try:
                    raw_fixtures = await endpoints.fetch_fixtures(
                        today_str, league_id, season, timezone=tz_name
                    )
                    api_calls += 1
                except Exception:
                    logger.exception("Error fetching fixtures league=%s", league_id)
                    league_results[league_id] = {"season": season, "fixtures": 0, "odds_rows": 0, "error": True}
                    continue

                league_fix = 0
                league_odds = 0

                for item in raw_fixtures:
                    result = await self._process_fixture(
                        item=item,
                        league_id=league_id,
                        season=season,
                        coverage=coverage,
                        standings_data=standings_data,
                        markets=markets,
                        preferred_bk_id=preferred_bk_id,
                    )
                    if result is None:
                        continue
                    internal_id, odds_count, calls = result
                    api_calls += calls
                    league_fix += 1
                    league_odds += odds_count

                logger.info(
                    "Phase C — league=%s season=%s → %d fixtures, %d cuotas",
                    league_id, season, league_fix, league_odds,
                )
                league_results[league_id] = {
                    "season": season,
                    "fixtures": league_fix,
                    "odds_rows": league_odds,
                }
                total_fixtures += league_fix
                total_odds += league_odds

            summary = {
                "date": today_str,
                "timezone": tz_name,
                "leagues_synced": len(league_seasons),
                "fixtures_synced": total_fixtures,
                "odds_rows_synced": total_odds,
                "by_league": league_results,
            }
            logger.info("Phase C completada: %s", summary)

            self._save_usage_snapshot(run_id, "/fixtures")
            sync_runs_repo.finish_sync_run(
                run_id,
                status="completed",
                leagues_synced=len(league_seasons),
                fixtures_synced=total_fixtures,
                odds_rows_synced=total_odds,
                api_calls_made=api_calls,
                summary_json=summary,
            )
            return summary

        except Exception as exc:
            logger.exception("Phase C falló: %s", exc)
            sync_runs_repo.finish_sync_run(
                run_id, status="failed",
                fixtures_synced=total_fixtures,
                odds_rows_synced=total_odds,
                api_calls_made=api_calls,
                error_message=str(exc),
            )
            raise

    # ── Phase D: Prematch sync (near kickoff) ─────────────────────────────────

    async def sync_prematch(self, fixture_ids: list[int]) -> dict:
        """Phase D: refresh odds and fetch lineups for fixtures near kickoff.

        Args:
            fixture_ids: Internal Supabase fixture IDs (filtered by kickoff window
                         in the calling script — e.g. kicking off within 2 hours).

        Returns:
            Summary dict with counts.
        """
        if not fixture_ids:
            logger.info("Phase D: no hay fixtures candidatos para prematch sync")
            return {"fixtures": 0, "odds_refreshed": 0, "lineups_fetched": 0}

        run_id = sync_runs_repo.start_sync_run("prematch")
        api_calls = 0
        odds_total = 0
        lineups_total = 0
        markets = settings.markets_list

        try:
            for fix_id in fixture_ids:
                fix = fixture_repo.get_fixture_by_internal_id(fix_id)
                if not fix:
                    continue

                provider_fix_id: int = fix["provider_fixture_id"]
                league_internal_id: int = fix["league_id"]

                # Get coverage for this fixture's league
                # (we need provider_league_id — query leagues table)
                league_result = get_supabase().table("leagues").select("provider_league_id, season, coverage").eq("id", league_internal_id).limit(1).execute()
                league_row = league_result.data[0] if league_result.data else {}
                coverage = league_row.get("coverage") or {}

                # Refresh odds
                if cov_helper.check(coverage, "odds", league_row.get("provider_league_id", 0)):
                    try:
                        odds_rows = await endpoints.fetch_odds(provider_fix_id, markets)
                        api_calls += 1
                        n = odds_repo.upsert_odds_batch(fix_id, odds_rows)
                        odds_total += n
                    except Exception:
                        logger.exception("Error refreshing odds fixture=%s", provider_fix_id)
                else:
                    logger.debug(
                        "coverage.odds=false para fixture=%s — odds refresh omitido", provider_fix_id
                    )

                # Fetch lineups (if coverage allows)
                if cov_helper.check_lineups(coverage, league_row.get("provider_league_id", 0), provider_fix_id):
                    try:
                        lineups = await endpoints.fetch_lineups(provider_fix_id)
                        api_calls += 1
                        if lineups:
                            # Store lineups in context_json (merge with existing)
                            existing_ctx = fix.get("context_json") or {}
                            existing_ctx["lineups"] = lineups
                            fixture_repo.update_fixture_context(fix_id, existing_ctx)
                            lineups_total += 1
                    except Exception:
                        logger.exception("Error fetching lineups fixture=%s", provider_fix_id)

            summary = {
                "fixtures": len(fixture_ids),
                "odds_refreshed": odds_total,
                "lineups_fetched": lineups_total,
            }
            logger.info("Phase D completada: %s", summary)
            self._save_usage_snapshot(run_id, "/fixtures/lineups")
            sync_runs_repo.finish_sync_run(
                run_id,
                status="completed",
                fixtures_synced=len(fixture_ids),
                odds_rows_synced=odds_total,
                api_calls_made=api_calls,
                summary_json=summary,
            )
            return summary

        except Exception as exc:
            logger.exception("Phase D falló: %s", exc)
            sync_runs_repo.finish_sync_run(
                run_id, status="failed", api_calls_made=api_calls, error_message=str(exc)
            )
            raise

    # ── Internal: per-fixture processing for Phase C ──────────────────────────

    async def _process_fixture(
        self,
        item: dict,
        league_id: int,
        season: int,
        coverage: dict,
        standings_data: list[dict],
        markets: list[str],
        preferred_bk_id: int | None,
    ) -> tuple[int, int, int] | None:
        """Upsert one fixture item and fetch its context + odds.

        Returns:
            (internal_fixture_id, odds_rows_count, api_calls_made) or None on error.
        """
        api_calls = 0
        try:
            fix = item["fixture"]
            league_data = item["league"]
            teams = item["teams"]
            provider_fix_id: int = fix["id"]

            # Upsert league + teams + fixture
            league_row = fixture_repo.upsert_league(
                provider_league_id=league_data["id"],
                name=league_data["name"],
                country=league_data.get("country"),
                season=int(league_data["season"]),
            )
            home_row = fixture_repo.upsert_team(
                provider_team_id=teams["home"]["id"],
                name=teams["home"]["name"],
                country=teams["home"].get("country"),
            )
            away_row = fixture_repo.upsert_team(
                provider_team_id=teams["away"]["id"],
                name=teams["away"]["name"],
                country=teams["away"].get("country"),
            )

            if not (league_row.get("id") and home_row.get("id") and away_row.get("id")):
                logger.warning("Upsert sin ID para fixture provider=%s", provider_fix_id)
                return None

            fixture_row = fixture_repo.upsert_fixture(
                provider_fixture_id=provider_fix_id,
                league_id=league_row["id"],
                home_team_id=home_row["id"],
                away_team_id=away_row["id"],
                kickoff_at=fix["date"],
                status=fix["status"]["short"],
                timezone=fix.get("timezone"),
                date_local=fix["date"][:10] if fix.get("date") else None,
                status_short=fix["status"].get("short"),
                status_long=fix["status"].get("long"),
                elapsed=fix["status"].get("elapsed"),
                venue_id=fix.get("venue", {}).get("id"),
            )
            internal_id: int = fixture_row.get("id")
            if not internal_id:
                return None

            # Build and store context blob
            context = await self._build_context(
                item=item,
                league_id=league_id,
                season=season,
                coverage=coverage,
                standings_data=standings_data,
                home_team_id=home_row["id"],
                away_team_id=away_row["id"],
                home_provider_id=teams["home"]["id"],
                away_provider_id=teams["away"]["id"],
            )
            api_calls += context.pop("_api_calls", 0)
            fixture_repo.update_fixture_context(internal_id, context)

            # Fetch and upsert odds (coverage-first)
            odds_count = 0
            if cov_helper.check(coverage, "odds", league_id, str(provider_fix_id)):
                try:
                    odds_rows = await endpoints.fetch_odds(
                        provider_fix_id, markets, bookmaker_id=preferred_bk_id
                    )
                    api_calls += 1
                    odds_count = odds_repo.upsert_odds_batch(internal_id, odds_rows)
                except Exception:
                    logger.exception("Error fetching odds fixture=%s", provider_fix_id)
            else:
                logger.debug(
                    "coverage.odds=false para league=%s — odds omitidos para fixture=%s",
                    league_id, provider_fix_id,
                )

            return internal_id, odds_count, api_calls

        except (KeyError, TypeError, ValueError):
            logger.exception("Error parseando fixture item: %s", item.get("fixture", {}).get("id"))
            return None

    async def _build_context(
        self,
        item: dict,
        league_id: int,
        season: int,
        coverage: dict,
        standings_data: list[dict],
        home_team_id: int,
        away_team_id: int,
        home_provider_id: int,
        away_provider_id: int,
    ) -> dict:
        """Build the context_json blob for a fixture.

        Returns a dict ready to store in fixtures.context_json.
        Includes a special "_api_calls" key (popped by the caller).
        """
        context: dict = {
            "home_team_provider_id": home_provider_id,
            "away_team_provider_id": away_provider_id,
            "_api_calls": 0,
        }
        provider_fix_id: int = item["fixture"]["id"]

        # Standing for each team (from already-fetched standings)
        if standings_data:
            context["home_standing"] = _find_standing(standings_data, home_provider_id)
            context["away_standing"] = _find_standing(standings_data, away_provider_id)

        # Team statistics (coverage-first)
        if cov_helper.check_fixture_stats(coverage, league_id):
            try:
                home_stats = await endpoints.fetch_team_statistics(home_provider_id, league_id, season)
                away_stats = await endpoints.fetch_team_statistics(away_provider_id, league_id, season)
                context["_api_calls"] += 2
                if home_stats:
                    context["home_stats"] = home_stats
                if away_stats:
                    context["away_stats"] = away_stats
            except Exception:
                logger.warning("team_statistics falló para fixture=%s — continuando", provider_fix_id)
        else:
            logger.debug(
                "coverage.fixtures.statistics_fixtures=false para league=%s — team_stats omitidos (fixture=%s)",
                league_id, provider_fix_id,
            )

        # Injuries (coverage-first)
        if cov_helper.check(coverage, "injuries", league_id, str(provider_fix_id)):
            try:
                injuries = await endpoints.fetch_injuries(provider_fix_id)
                context["_api_calls"] += 1
                context["injuries"] = injuries
            except Exception:
                logger.warning("injuries falló para fixture=%s — continuando", provider_fix_id)
        else:
            logger.debug(
                "coverage.injuries=false para league=%s — injuries omitidos (fixture=%s)",
                league_id, provider_fix_id,
            )

        # Provider predictions (coverage-first, advisory only)
        if cov_helper.check(coverage, "predictions", league_id, str(provider_fix_id)):
            try:
                pred = await endpoints.fetch_provider_predictions(provider_fix_id)
                context["_api_calls"] += 1
                if pred:
                    context["provider_prediction"] = pred
            except Exception:
                logger.warning("provider predictions falló para fixture=%s — continuando", provider_fix_id)
        else:
            logger.debug(
                "coverage.predictions=false para league=%s — provider predictions omitidos (fixture=%s)",
                league_id, provider_fix_id,
            )

        # H2H from DB (no API call needed — uses already-stored fixtures)
        h2h = fixture_repo.get_h2h_fixtures(home_team_id, away_team_id, limit=5)
        if h2h:
            context["h2h"] = h2h

        return context

    # ── Internal: compatibility shim ─────────────────────────────────────────

    async def sync_today(
        self,
        league_seasons: dict[int, int],
        timezone: str | None = None,
    ) -> dict:
        """Alias for sync_daily — kept for backwards compatibility with sync_today.py."""
        return await self.sync_daily(league_seasons, timezone=timezone)

    # ── Utility ───────────────────────────────────────────────────────────────

    def _save_usage_snapshot(self, run_id: int | None, endpoint: str) -> None:
        """Persist the current rate-limit state as a snapshot row."""
        from app.data.api_football.client import get_rate_limit_state
        rate_state = get_rate_limit_state()
        sync_runs_repo.save_usage_snapshot(rate_state, endpoint=endpoint, sync_run_id=run_id)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _find_standing(standings: list[dict], provider_team_id: int) -> dict:
    """Find a team's standing entry by provider team ID."""
    for entry in standings:
        team = entry.get("team", {})
        if team.get("id") == provider_team_id:
            return {
                "rank": entry.get("rank"),
                "points": entry.get("points"),
                "played": entry.get("all", {}).get("played"),
                "won": entry.get("all", {}).get("win"),
                "drawn": entry.get("all", {}).get("draw"),
                "lost": entry.get("all", {}).get("lose"),
                "goals_for": entry.get("all", {}).get("goals", {}).get("for"),
                "goals_against": entry.get("all", {}).get("goals", {}).get("against"),
                "goal_diff": entry.get("goalsDiff"),
                "form": entry.get("form"),
                "status": entry.get("status"),
                "description": entry.get("description"),
            }
    return {}


sync_service = SyncService()
