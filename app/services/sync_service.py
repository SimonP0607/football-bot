"""Sync service: pulls fixtures and odds from API-Football into Supabase."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.data.api_football import endpoints
from app.data.repositories import fixture_repo, odds_repo

logger = logging.getLogger(__name__)


class SyncService:
    """Orchestrates the full data ingestion pipeline for a given day."""

    async def sync_today(self, league_ids: list[int], season: int) -> dict:
        """Sync today's fixtures and their pre-match odds for all given leagues.

        Steps:
        1. Resolve today's date in the configured timezone.
        2. For each league: fetch fixtures from API-Football, upsert to Supabase.
        3. For each fixture: fetch odds, upsert to Supabase.

        Args:
            league_ids: API-Football league IDs to sync (e.g. [39, 140]).
            season: Four-digit season year (e.g. 2024).

        Returns:
            Summary dict with counts of synced fixtures and odds rows.
        """
        tz = ZoneInfo(settings.default_timezone)
        today_str = datetime.now(tz).strftime("%Y-%m-%d")
        markets = settings.markets_list

        total_fixtures = 0
        total_odds = 0

        for league_id in league_ids:
            logger.info("Sync league=%s season=%s date=%s", league_id, season, today_str)
            try:
                raw_fixtures = await endpoints.fetch_fixtures(today_str, league_id, season)
            except Exception:
                logger.exception("Error al fetchar fixtures de league=%s", league_id)
                continue

            for item in raw_fixtures:
                internal_fixture_id = self._upsert_fixture_item(item)
                if internal_fixture_id is None:
                    continue
                total_fixtures += 1

                provider_fixture_id: int = item["fixture"]["id"]
                try:
                    odds_rows = await endpoints.fetch_odds(provider_fixture_id, markets)
                except Exception:
                    logger.exception("Error al fetchar odds de fixture=%s", provider_fixture_id)
                    continue

                n = odds_repo.upsert_odds_batch(internal_fixture_id, odds_rows)
                total_odds += n

        summary = {
            "date": today_str,
            "leagues_synced": len(league_ids),
            "fixtures_synced": total_fixtures,
            "odds_rows_synced": total_odds,
        }
        logger.info("Sync completado: %s", summary)
        return summary

    def _upsert_fixture_item(self, item: dict) -> int | None:
        """Parse one API-Football fixture item and upsert league, teams, fixture.

        Returns the internal Supabase fixture ID, or None on error.

        API-Football fixture item structure::

            {
                "fixture": {"id": 12345, "date": "2024-01-15T20:00:00+00:00",
                            "status": {"short": "NS"}},
                "league":  {"id": 39, "name": "Premier League",
                            "country": "England", "season": 2024},
                "teams":   {
                    "home": {"id": 33, "name": "Manchester United", "country": "England"},
                    "away": {"id": 40, "name": "Liverpool",         "country": "England"},
                },
            }
        """
        try:
            fix = item["fixture"]
            league_data = item["league"]
            teams = item["teams"]

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
                logger.warning(
                    "Upsert retornó sin ID para fixture provider_id=%s", fix["id"]
                )
                return None

            fixture_row = fixture_repo.upsert_fixture(
                provider_fixture_id=fix["id"],
                league_id=league_row["id"],
                home_team_id=home_row["id"],
                away_team_id=away_row["id"],
                kickoff_at=fix["date"],
                status=fix["status"]["short"],
            )
            return fixture_row.get("id")

        except (KeyError, TypeError, ValueError):
            logger.exception("Error parseando fixture item: %s", item)
            return None


sync_service = SyncService()
