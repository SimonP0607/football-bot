"""Prediction service: runs the model pipeline and persists picks."""

import logging
from app.core.config import settings
from app.data.repositories import fixture_repo, odds_repo, prediction_repo
from app.model.predictors.odds_predictor import OddsPredictor
from app.model.filters.pick_filter import PickFilter

logger = logging.getLogger(__name__)


class PredictionService:
    """Runs the full prediction pipeline for today's fixtures."""

    def __init__(self) -> None:
        self._predictor = OddsPredictor()
        self._filter = PickFilter()

    def run_for_today(self) -> dict:
        """Calculate and persist picks for all of today's fixtures.

        Flow:
        1. Fetch today's fixtures from Supabase (already synced).
        2. For each fixture: load odds, run predictor, apply filter.
        3. Persist publishable picks; mark the rest as not publishable.

        Returns:
            Summary dict with counts.
        """
        fixtures = fixture_repo.get_fixtures_today()
        if not fixtures:
            logger.info("Sin fixtures para hoy — prediction pipeline omitido")
            return {"fixtures": 0, "picks_saved": 0}

        total_picks = 0
        for fix in fixtures:
            fixture_id: int = fix["id"]
            odds_rows = odds_repo.get_odds_for_fixture(fixture_id)
            if not odds_rows:
                logger.debug("fixture_id=%s sin cuotas, omitido", fixture_id)
                continue

            candidates = self._predictor.calculate(odds_rows)
            publishable = self._filter.apply(
                candidates,
                min_edge=settings.min_edge,
                min_confidence=settings.min_confidence,
                max_picks=settings.max_daily_picks,
            )
            publishable_selections = {(p.market, p.selection) for p in publishable}

            # Persist all candidates; only the filtered ones are marked publishable.
            for candidate in candidates:
                is_pub = (candidate.market, candidate.selection) in publishable_selections
                prediction_repo.save_prediction(
                    fixture_id=candidate.fixture_id,
                    market=candidate.market,
                    recommended_pick=candidate.selection,
                    model_probability=candidate.model_probability,
                    implied_probability=candidate.implied_probability,
                    edge=candidate.edge,
                    confidence_score=candidate.confidence_score,
                    argument_json=candidate.argument_json,
                    is_publishable=is_pub,
                )
            total_picks += len(publishable)
            logger.info(
                "fixture_id=%s → %d candidatos, %d picks publicables",
                fixture_id, len(candidates), len(publishable),
            )

        summary = {"fixtures": len(fixtures), "picks_saved": total_picks}
        logger.info("Prediction pipeline completado: %s", summary)
        return summary

    def get_today_picks(self) -> tuple[list[dict], list[dict]]:
        """Return (predictions, fixtures) for today's publishable picks.

        Returns a tuple so the formatter has both the pick data and the fixture
        context (team names, kickoff time) without coupling the repo to the formatter.
        """
        fixtures = fixture_repo.get_fixtures_today()
        fixture_ids = [f["id"] for f in fixtures]
        predictions = prediction_repo.get_publishable_today(fixture_ids)
        return predictions, fixtures

    def get_top_picks(self, limit: int = 5) -> tuple[list[dict], list[dict]]:
        """Return (predictions, fixtures) for the top picks by confidence."""
        fixtures = fixture_repo.get_fixtures_today()
        fixture_ids = [f["id"] for f in fixtures]
        predictions = prediction_repo.get_top_picks(fixture_ids, limit=limit)
        return predictions, fixtures

    def get_estado(self) -> dict:
        """Return a status summary for the /estado command."""
        fixtures = fixture_repo.get_fixtures_today()
        fixture_ids = [f["id"] for f in fixtures]
        return {
            "fixtures_today": len(fixtures),
            "odds_rows": odds_repo.count_odds_today(fixture_ids),
            "picks_today": prediction_repo.count_predictions_today(fixture_ids),
        }


prediction_service = PredictionService()
