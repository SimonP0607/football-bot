"""Prediction service: runs the full scoring pipeline and persists picks.

v2 pipeline:
  1. OddsPredictor: bookmaker-consensus → model_probability (market signal)
  2. MatchContextScorer: standings, form, stats, h2h, provider → small adjustment
  3. Merge: final_probability = model_probability + adjustment (capped ±5pp)
  4. Enrich argument_json with reasons, main_risk, and all context signals
  5. PickFilter: apply edge + confidence thresholds, cap max_daily_picks
"""

import logging
from dataclasses import replace

from app.core.config import settings
from app.data.repositories import fixture_repo, odds_repo, prediction_repo
from app.model.predictors.odds_predictor import OddsPredictor, PredictionCandidate
from app.model.predictors.match_scorer import score_market
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
        1. Fetch today's fixtures (already synced, with context_json).
        2. For each fixture: load odds, run OddsPredictor, apply MatchContextScorer.
        3. Merge signals into enriched argument_json.
        4. Apply PickFilter and persist all candidates.

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

            context = fix.get("context_json") or {}

            # v1: odds consensus model
            candidates = self._predictor.calculate(odds_rows)
            if not candidates:
                continue

            # v2: enrich each candidate with context signals
            enriched = [
                self._enrich_candidate(c, context)
                for c in candidates
            ]

            # Apply filter (edge + confidence thresholds + max picks cap)
            publishable = self._filter.apply(
                enriched,
                min_edge=settings.min_edge,
                min_confidence=settings.min_confidence,
                max_picks=settings.max_daily_picks,
            )
            publishable_set = {(p.market, p.selection) for p in publishable}

            for candidate in enriched:
                is_pub = (candidate.market, candidate.selection) in publishable_set
                prediction_repo.save_prediction(
                    fixture_id=candidate.fixture_id,
                    market=candidate.market,
                    selection=candidate.selection,
                    model_probability=candidate.model_probability,
                    implied_probability=candidate.implied_probability,
                    edge=candidate.edge,
                    confidence_score=candidate.confidence_score,
                    argument_json=candidate.argument_json,
                    is_publishable=is_pub,
                )

            total_picks += len(publishable)
            logger.info(
                "fixture_id=%s → %d candidatos, %d publicables",
                fixture_id, len(enriched), len(publishable),
            )

        summary = {"fixtures": len(fixtures), "picks_saved": total_picks}
        logger.info("Prediction pipeline completado: %s", summary)
        return summary

    def _enrich_candidate(
        self, candidate: PredictionCandidate, context: dict
    ) -> PredictionCandidate:
        """Apply MatchContextScorer to a candidate and return an enriched copy.

        The enrichment:
        - Adds reasons[], main_risk, and context signals to argument_json.
        - Applies a small probability_adjustment to model_probability.
        - Recalculates edge with the adjusted probability.
        """
        if not context:
            return candidate

        try:
            scored = score_market(candidate.market, candidate.selection, context)
        except Exception as exc:
            logger.warning(
                "MatchContextScorer falló para fixture=%s market=%s: %s",
                candidate.fixture_id, candidate.market, exc,
            )
            return candidate

        adj = scored.get("probability_adjustment", 0.0)
        new_prob = round(
            max(0.01, min(0.99, candidate.model_probability + adj)), 4
        )
        new_edge = round(new_prob - candidate.implied_probability, 4)

        enriched_arg = dict(candidate.argument_json)
        enriched_arg["reasons"] = scored.get("reasons", [])
        enriched_arg["main_risk"] = scored.get("main_risk", "")
        enriched_arg["context_signals"] = scored.get("signals", [])
        enriched_arg["probability_adjustment"] = adj
        enriched_arg["base_model_probability"] = candidate.model_probability

        return replace(
            candidate,
            model_probability=new_prob,
            confidence_score=new_prob,
            edge=new_edge,
            argument_json=enriched_arg,
        )

    # ── Query methods (unchanged interface) ──────────────────────────────────

    def get_today_picks(self) -> tuple[list[dict], list[dict]]:
        """Return (predictions, fixtures) for today's publishable picks."""
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

    def analyze_fixture(self, fixture: dict) -> list[dict]:
        """Run the full prediction pipeline for a single fixture (for /partido).

        Does not persist results — returns candidates directly.

        Returns:
            List of enriched PredictionCandidate dicts (all markets, all selections).
        """
        fixture_id: int = fixture["id"]
        odds_rows = odds_repo.get_odds_for_fixture(fixture_id)
        if not odds_rows:
            return []

        context = fixture.get("context_json") or {}
        candidates = self._predictor.calculate(odds_rows)
        enriched = [self._enrich_candidate(c, context) for c in candidates]
        return [
            {
                "fixture_id": c.fixture_id,
                "market_key": c.market,
                "selection": c.selection,
                "model_probability": c.model_probability,
                "implied_probability": c.implied_probability,
                "edge": c.edge,
                "confidence_score": c.confidence_score,
                "best_odd": c.best_odd,
                "best_bookmaker": c.best_bookmaker,
                "argument_json": c.argument_json,
            }
            for c in enriched
        ]


prediction_service = PredictionService()
