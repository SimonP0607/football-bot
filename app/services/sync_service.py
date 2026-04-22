"""Sync service: multi-phase data ingestion from API-Football into Supabase.

Phase A  reference sync   — bookmakers, bet types, timezone validation
Phase B  bootstrap sync   — league coverage metadata (run once per season)
Phase C  daily sync       — fixtures + context + odds (coverage-first, tier-aware)
Phase D  prematch sync    — lineups + odds refresh near kickoff

Coverage-first principle:
  Before calling standings, injuries, predictions, or odds, the service reads
  the stored competition_seasons.coverage. If a flag is false the call is
  skipped and a debug trace is logged so every omission is auditable.

Tier-aware sync (Phase C):
  tier_1_daily    — full enrichment on every run
  tier_2_matchday — full enrichment but standings only when fixtures exist
  tier_3_light    — fixtures + odds only; no standings, team stats, injuries,
                    or provider predictions (saves requests for minor leagues)
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.data.api_football import endpoints, coverage as cov_helper
from app.data.api_football.client import APIFootballError
from app.data.repositories import fixture_repo, odds_repo
from app.data.repositories import reference_repo, sync_runs_repo

logger = logging.getLogger(__name__)


class SyncService:
    """Orchestrates all sync phases for API-Football data ingestion."""

    # ── Phase A: Reference data ───────────────────────────────────────────────

    async def sync_reference(self) -> dict:
        """Phase A: populate ref_bookmakers and ref_bet_types, validate timezone.

        Safe to run repeatedly — all upserts are idempotent.
        """
        run_id = sync_runs_repo.start_sync_run("reference")
        api_calls = 0
        try:
            tz = settings.default_timezone
            valid = await endpoints.validate_timezone(tz)
            api_calls += 1
            if not valid:
                logger.warning("Timezone '%s' no válida para API-Football", tz)

            bookmakers = await endpoints.fetch_bookmakers()
            api_calls += 1
            n_bk = reference_repo.upsert_bookmakers(bookmakers)

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

    async def sync_bootstrap(self, league_seasons: dict[int, int] | None = None) -> dict:
        """Phase B: fetch league metadata + coverage from /leagues.

        Stores coverage in competition_seasons.coverage so Phase C can skip
        unavailable endpoints. Run once per season or when adding new leagues.

        Season resolution order (highest to lowest priority):
          1. Explicit league_seasons argument.
          2. LEAGUE_SEASONS from settings (.env), applied as overrides.
          3. Auto-discovery via /leagues?current=true filtered to DEFAULT_LEAGUE_IDS.
        """
        effective: dict[int, int] = {}

        if league_seasons:
            effective = dict(league_seasons)
        else:
            league_ids = settings.league_ids_list or None
            if not league_ids:
                logger.error(
                    "Phase B: no hay ligas configuradas. "
                    "Configura DEFAULT_LEAGUE_IDS en .env o usa --leagues 39:2025,...",
                )
                return {"leagues_updated": 0, "leagues_skipped": 0, "error": "no_league_ids"}

            logger.info(
                "Phase B: auto-descubriendo temporadas via /leagues?current=true | ligas=%s",
                league_ids,
            )
            try:
                discovered = await endpoints.fetch_active_leagues(league_ids)
                for entry in discovered:
                    effective[entry["league_id"]] = entry["season"]
                logger.info("Phase B: %d ligas descubiertas: %s", len(effective), effective)
            except APIFootballError as exc:
                logger.warning("Phase B: auto-discovery falló: %s", exc)
            except Exception as exc:
                logger.warning("Phase B: auto-discovery falló inesperadamente: %s", exc)

            # Apply LEAGUE_SEASONS overrides from .env
            for lid, season_override in settings.league_seasons_map.items():
                if lid in effective and effective[lid] != season_override:
                    logger.info(
                        "Phase B: override season liga=%s: %s → %s",
                        lid, effective[lid], season_override,
                    )
                effective[lid] = season_override

            if not effective:
                logger.error(
                    "Phase B: no se pudo resolver ninguna temporada. "
                    "Configura LEAGUE_SEASONS en .env o verifica DEFAULT_LEAGUE_IDS.",
                )
                return {"leagues_updated": 0, "leagues_skipped": 0, "error": "no_seasons_resolved"}

        run_id = sync_runs_repo.start_sync_run("bootstrap")
        api_calls = 0
        updated = 0
        skipped_api = 0
        skipped_no_data = 0
        try:
            for league_id, season in effective.items():
                try:
                    meta = await endpoints.fetch_league_coverage(league_id, season)
                    api_calls += 1
                except APIFootballError as exc:
                    api_calls += 1
                    error_text = str(exc).lower()
                    if "free plan" in error_text or "free plans" in error_text:
                        logger.warning(
                            "Phase B: league=%s season=%s — plan gratuito no permite esta temporada. "
                            "Error: %s",
                            league_id, season, exc,
                        )
                    else:
                        logger.warning(
                            "Phase B: league=%s season=%s — error de API: %s — continuando",
                            league_id, season, exc,
                        )
                    skipped_api += 1
                    continue
                except Exception as exc:
                    api_calls += 1
                    logger.warning(
                        "Phase B: league=%s season=%s — error inesperado: %s — continuando",
                        league_id, season, exc,
                    )
                    skipped_api += 1
                    continue

                if not meta:
                    logger.warning(
                        "Phase B: sin datos de API para league=%s season=%s — "
                        "¿el ID es correcto? ¿existe la temporada?",
                        league_id, season,
                    )
                    skipped_no_data += 1
                    continue

                if not meta.get("current", False):
                    logger.warning(
                        "Phase B: league=%s season=%s — la API indica que %s NO es la "
                        "temporada activa. Verifica DEFAULT_SEASON o usa "
                        "--leagues %s:<temporada_correcta>.",
                        league_id, season, season, league_id,
                    )

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
                    "Phase B: league=%s season=%s → coverage guardada "
                    "(standings=%s injuries=%s predictions=%s odds=%s current=%s)",
                    league_id, season,
                    meta.get("coverage", {}).get("standings"),
                    meta.get("coverage", {}).get("injuries"),
                    meta.get("coverage", {}).get("predictions"),
                    meta.get("coverage", {}).get("odds"),
                    meta.get("current", False),
                )

            summary = {
                "leagues_resolved": len(effective),
                "leagues_updated": updated,
                "leagues_skipped_api_error": skipped_api,
                "leagues_skipped_no_data": skipped_no_data,
            }
            final_status = "completed" if updated > 0 else "completed_with_warnings"
            sync_runs_repo.finish_sync_run(
                run_id,
                status=final_status,
                leagues_synced=updated,
                api_calls_made=api_calls,
                summary_json=summary,
            )
            logger.info("Phase B completada: %s", summary)
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
        league_seasons: dict[int, int] | None = None,
        timezone: str | None = None,
    ) -> dict:
        """Phase C: sync today's fixtures + context + odds.

        League source (priority order):
          1. Explicit league_seasons argument (from --leagues CLI flag).
          2. tracked_competitions WHERE is_active=True (primary config source).
          3. competition_seasons WHERE current=True (legacy fallback).

        Tier behaviour per league:
          tier_1_daily    — standings + full enrichment always
          tier_2_matchday — same as tier_1 but standings only when fixtures present
          tier_3_light    — fixtures + odds only (no standings/stats/injuries/predictions)

        Returns:
            Summary dict with counts by league.
        """
        # ── Resolve which leagues to sync ────────────────────────────────────
        if league_seasons:
            # Explicit CLI override — treat all as tier_1
            tracked: dict[int, dict] = {
                lid: {"season": s, "sync_tier": "tier_1_daily"}
                for lid, s in league_seasons.items()
            }
            logger.info("Phase C: leagues explícitas=%s", list(tracked.keys()))
        else:
            tracked = fixture_repo.get_tracked_league_seasons()
            if not tracked:
                # Legacy fallback: read from competition_seasons.current=True
                logger.warning(
                    "Phase C: tracked_competitions vacío — "
                    "usando competition_seasons.current=True como fallback. "
                    "Ejecuta el seed: sql/seed_tracked_competitions.sql"
                )
                old_map = fixture_repo.get_active_league_seasons(
                    settings.league_ids_list or None
                )
                tracked = {
                    lid: {"season": s, "sync_tier": "tier_1_daily"}
                    for lid, s in old_map.items()
                }
            if not tracked:
                logger.error(
                    "Phase C: sin ligas activas. "
                    "Ejecuta Phase B y el seed de tracked_competitions."
                )
                return {"error": "no_active_leagues", "fixtures_synced": 0, "odds_rows_synced": 0}

            # Apply LEAGUE_SEASONS overrides from .env (season only, keep tier)
            for lid, season_override in settings.league_seasons_map.items():
                if lid in tracked:
                    tracked[lid] = {**tracked[lid], "season": season_override}

            logger.info(
                "Phase C: %d ligas activas en tracked_competitions", len(tracked)
            )

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
            "=== Phase C — Sync diario | fecha=%s | timezone=%s | ligas=%d ===",
            today_str, tz_name, len(tracked),
        )

        league_results: dict[int, dict] = {}

        try:
            for league_id, meta in tracked.items():
                season: int = meta["season"]
                sync_tier: str = meta.get("sync_tier", "tier_1_daily")

                # Read coverage from DB (populated during Phase B)
                coverage = fixture_repo.get_league_coverage(league_id, season)
                if coverage is None:
                    logger.warning(
                        "Phase C: liga=%s season=%s sin coverage en BD — "
                        "ejecuta Phase B primero. Continuando sin coverage.",
                        league_id, season,
                    )
                    coverage = {}

                # Fetch today's fixtures first (always — needed to know if game exists)
                try:
                    raw_fixtures = await endpoints.fetch_fixtures(
                        today_str, league_id, season, timezone=tz_name
                    )
                    api_calls += 1
                except Exception:
                    logger.exception("Error fetching fixtures league=%s", league_id)
                    league_results[league_id] = {
                        "season": season, "tier": sync_tier,
                        "fixtures": 0, "odds_rows": 0, "error": True,
                    }
                    continue

                # tier_2_matchday: skip standings if no fixtures today
                # tier_1_daily: fetch standings regardless (context for upcoming fixtures)
                # tier_3_light: never fetch standings
                standings_data: list[dict] = []
                should_fetch_standings = (
                    sync_tier != "tier_3_light"
                    and (sync_tier == "tier_1_daily" or len(raw_fixtures) > 0)
                    and cov_helper.check(coverage, "standings", league_id)
                )
                if should_fetch_standings:
                    try:
                        standings_data = await endpoints.fetch_standings(league_id, season)
                        api_calls += 1
                    except Exception:
                        logger.warning(
                            "standings falló para league=%s — continuando sin él", league_id
                        )
                elif sync_tier == "tier_3_light":
                    logger.debug(
                        "tier_3_light: standings omitidos para league=%s", league_id
                    )

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
                        sync_tier=sync_tier,
                    )
                    if result is None:
                        continue
                    _, odds_count, calls = result
                    api_calls += calls
                    league_fix += 1
                    league_odds += odds_count

                logger.info(
                    "Phase C — league=%s season=%s tier=%s → %d fixtures, %d cuotas",
                    league_id, season, sync_tier, league_fix, league_odds,
                )
                league_results[league_id] = {
                    "season": season,
                    "tier": sync_tier,
                    "fixtures": league_fix,
                    "odds_rows": league_odds,
                }
                total_fixtures += league_fix
                total_odds += league_odds

            summary = {
                "date": today_str,
                "timezone": tz_name,
                "leagues_synced": len(tracked),
                "fixtures_synced": total_fixtures,
                "odds_rows_synced": total_odds,
                "by_league": league_results,
            }
            logger.info("Phase C completada: %s", summary)
            self._save_usage_snapshot(run_id, "/fixtures")
            sync_runs_repo.finish_sync_run(
                run_id,
                status="completed",
                leagues_synced=len(tracked),
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
        """Phase D: refresh odds and fetch lineups for fixtures near kickoff."""
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

                league_row = fixture_repo.get_competition_season_by_id(league_internal_id)
                coverage = (league_row or {}).get("coverage") or {}
                provider_lid = (league_row or {}).get("provider_league_id", 0)

                # Refresh odds
                if cov_helper.check(coverage, "odds", provider_lid):
                    try:
                        odds_rows = await endpoints.fetch_odds(provider_fix_id, markets)
                        api_calls += 1
                        n = odds_repo.upsert_odds_batch(fix_id, odds_rows)
                        odds_total += n
                    except Exception:
                        logger.exception("Error refreshing odds fixture=%s", provider_fix_id)
                else:
                    logger.debug(
                        "coverage.odds=false para fixture=%s — odds refresh omitido",
                        provider_fix_id,
                    )

                # Fetch lineups
                if cov_helper.check_lineups(coverage, provider_lid, provider_fix_id):
                    try:
                        lineups = await endpoints.fetch_lineups(provider_fix_id)
                        api_calls += 1
                        if lineups:
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
        sync_tier: str = "tier_1_daily",
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

            # Upsert league (competition + competition_season) and teams
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

            # Note: venue_id is intentionally not stored — it would require upserting
            # the venues table first. venue is non-critical for predictions.
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
            )
            internal_id: int = fixture_row.get("id")
            if not internal_id:
                return None

            # Build and store context blob (tier-aware)
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
                sync_tier=sync_tier,
            )
            api_calls += context.pop("_api_calls", 0)
            fixture_repo.update_fixture_context(internal_id, context)

            # Fetch and upsert odds (coverage-first, all tiers)
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
                    "coverage.odds=false para league=%s fixture=%s — odds omitidos",
                    league_id, provider_fix_id,
                )

            return internal_id, odds_count, api_calls

        except Exception:
            logger.exception(
                "Error procesando fixture provider=%s league=%s — continuando",
                item.get("fixture", {}).get("id"), league_id,
            )
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
        sync_tier: str = "tier_1_daily",
    ) -> dict:
        """Build the context_json blob for a fixture.

        Returns a dict ready to store in fixture_contexts.context_json.
        Includes a special "_api_calls" key (popped by the caller).

        For tier_3_light, returns immediately with minimal data (no API calls).
        """
        context: dict = {
            "home_team_provider_id": home_provider_id,
            "away_team_provider_id": away_provider_id,
            "_api_calls": 0,
        }
        provider_fix_id: int = item["fixture"]["id"]

        # tier_3_light: skip all enrichment — fixtures + odds only
        if sync_tier == "tier_3_light":
            logger.debug(
                "tier_3_light: enrichment omitido para fixture=%s", provider_fix_id
            )
            return context

        # Standings (pre-fetched once per league — just look up each team)
        if standings_data:
            context["home_standing"] = _find_standing(standings_data, home_provider_id)
            context["away_standing"] = _find_standing(standings_data, away_provider_id)

        # Team statistics (coverage-first)
        if cov_helper.check_fixture_stats(coverage, league_id):
            try:
                home_stats = await endpoints.fetch_team_statistics(
                    home_provider_id, league_id, season
                )
                away_stats = await endpoints.fetch_team_statistics(
                    away_provider_id, league_id, season
                )
                context["_api_calls"] += 2
                if home_stats:
                    context["home_stats"] = home_stats
                if away_stats:
                    context["away_stats"] = away_stats
            except Exception:
                logger.warning(
                    "team_statistics falló para fixture=%s — continuando", provider_fix_id
                )
        else:
            logger.debug(
                "coverage.fixtures.statistics_fixtures=false para league=%s — "
                "team_stats omitidos (fixture=%s)",
                league_id, provider_fix_id,
            )

        # Injuries (coverage-first)
        if cov_helper.check(coverage, "injuries", league_id, str(provider_fix_id)):
            try:
                injuries = await endpoints.fetch_injuries(provider_fix_id)
                context["_api_calls"] += 1
                context["injuries"] = injuries
            except Exception:
                logger.warning(
                    "injuries falló para fixture=%s — continuando", provider_fix_id
                )
        else:
            logger.debug(
                "coverage.injuries=false para league=%s fixture=%s — injuries omitidos",
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
                logger.warning(
                    "provider predictions falló para fixture=%s — continuando",
                    provider_fix_id,
                )
        else:
            logger.debug(
                "coverage.predictions=false para league=%s fixture=%s — "
                "provider predictions omitidos",
                league_id, provider_fix_id,
            )

        # H2H from DB (no API call — uses stored fixtures)
        h2h = fixture_repo.get_h2h_fixtures(home_team_id, away_team_id, limit=5)
        if h2h:
            context["h2h"] = h2h

        return context

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
