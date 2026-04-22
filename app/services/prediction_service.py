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
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.data.repositories import fixture_repo, odds_repo, prediction_repo
from app.model.predictors.odds_predictor import OddsPredictor, PredictionCandidate
from app.model.predictors.match_scorer import score_market
from app.model.filters.pick_filter import PickFilter

logger = logging.getLogger(__name__)


def _is_prematch(fix: dict, lead_minutes: int) -> bool:
    """Return True only if the fixture is not yet started and kicks off in more
    than ``lead_minutes`` minutes from now.

    status_short values that mean "not started": NS, TBD, or empty/None.
    Any other value (1H, HT, 2H, ET, BT, P, SUSP, INT, FT, AET, PEN, ABD,
    AWD, WO) means the match is in progress or finished.
    """
    if fix.get("status_short") not in (None, "", "NS", "TBD"):
        return False
    kickoff = fix.get("kickoff_at")
    if not kickoff:
        return True  # unknown kickoff → keep it (safe default)
    try:
        tz = ZoneInfo(settings.default_timezone)
        now = datetime.now(tz)
        ko = datetime.fromisoformat(kickoff).astimezone(tz)
        return (ko - now).total_seconds() >= lead_minutes * 60
    except Exception:
        return True  # parse error → keep it


class PredictionService:
    """Runs the full prediction pipeline for today's fixtures."""

    def __init__(self) -> None:
        self._predictor = OddsPredictor()
        self._filter = PickFilter()

    def run_for_today(self) -> dict:
        """Calculate and persist picks for all of today's prematch fixtures.

        Flow:
        1. Fetch today's fixtures (already synced, with context_json).
        2. Filter to prematch-only (not started, >= PREMATCH_MIN_LEAD_MINUTES).
        3. For each fixture: load odds, run OddsPredictor, apply MatchContextScorer.
        4. Merge signals into enriched argument_json.
        5. Apply PickFilter and persist all candidates.

        Returns:
            Summary dict with pipeline counters.
        """
        all_fixtures = fixture_repo.get_fixtures_today()
        if not all_fixtures:
            logger.info("Sin fixtures para hoy — prediction pipeline omitido")
            return {
                "fixtures_fetched": 0,
                "discarded_started": 0,
                "discarded_no_odds": 0,
                "candidates_generated": 0,
                "publishable_saved": 0,
            }

        lead = settings.prematch_min_lead_minutes
        prematch_fixtures = [f for f in all_fixtures if _is_prematch(f, lead)]
        discarded_started = len(all_fixtures) - len(prematch_fixtures)
        if discarded_started:
            logger.info(
                "Prematch filter: %d/%d fixtures descartados (iniciados o en <%d min)",
                discarded_started, len(all_fixtures), lead,
            )

        discarded_no_odds = 0
        total_candidates = 0
        total_picks = 0

        for fix in prematch_fixtures:
            fixture_id: int = fix["id"]
            odds_rows = odds_repo.get_odds_for_fixture(fixture_id)
            if not odds_rows:
                logger.debug("fixture_id=%s sin cuotas, omitido", fixture_id)
                discarded_no_odds += 1
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
            total_candidates += len(enriched)

            # Log enriched candidates before filter (DEBUG to avoid noise in prod)
            logger.debug(
                "fixture_id=%s — candidatos pre-filtro (%d):", fixture_id, len(enriched)
            )
            for c in enriched:
                logger.debug(
                    "  %s/%s: model_prob=%.4f implied=%.4f edge=%.4f "
                    "conf=%.4f best_odd=%.2f (%s)",
                    c.market, c.selection,
                    c.model_probability, c.implied_probability,
                    c.edge, c.confidence_score, c.best_odd, c.best_bookmaker,
                )

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

            # Register publishable picks in pick_results (pending settlement).
            # Import here to avoid circular import at module level.
            from app.data.repositories import settlement_repo
            for candidate in enriched:
                if (candidate.market, candidate.selection) in publishable_set:
                    saved = prediction_repo.get_candidate_id(
                        candidate.fixture_id, candidate.market, candidate.selection
                    )
                    if saved:
                        settlement_repo.create_pending(
                            pick_candidate_id=saved,
                            fixture_id=candidate.fixture_id,
                            market_key=candidate.market,
                            selection=candidate.selection,
                            odd_taken=candidate.best_odd,
                        )

            total_picks += len(publishable)
            logger.info(
                "fixture_id=%s → %d candidatos, %d publicables "
                "(min_edge=%.2f, min_conf=%.2f)",
                fixture_id, len(enriched), len(publishable),
                settings.min_edge, settings.min_confidence,
            )

        summary = {
            "fixtures_fetched": len(all_fixtures),
            "discarded_started": discarded_started,
            "discarded_no_odds": discarded_no_odds,
            "candidates_generated": total_candidates,
            "publishable_saved": total_picks,
        }
        logger.info(
            "Pipeline summary | fetched=%d started=%d no_odds=%d "
            "candidates=%d publishable=%d",
            summary["fixtures_fetched"], summary["discarded_started"],
            summary["discarded_no_odds"], summary["candidates_generated"],
            summary["publishable_saved"],
        )
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

    # ── Query methods ─────────────────────────────────────────────────────────

    def get_today_picks(self) -> tuple[list[dict], list[dict]]:
        """Return (predictions, fixtures) for today's publishable prematch picks.

        predictions are ordered by kickoff (chronological).
        fixtures list contains only prematch fixtures.
        """
        all_fixtures = fixture_repo.get_fixtures_today()
        lead = settings.prematch_min_lead_minutes
        prematch = [f for f in all_fixtures if _is_prematch(f, lead)]
        fixture_ids = [f["id"] for f in sorted(prematch, key=lambda f: f.get("kickoff_at", ""))]
        predictions = prediction_repo.get_publishable_today(fixture_ids)
        return predictions, prematch

    def get_top_picks(self, limit: int | None = None) -> tuple[list[dict], list[dict]]:
        """Return (predictions, fixtures) for the top picks by quality score.

        Uses settings.top_picks_limit when limit is None.
        fixtures list contains only prematch fixtures.
        """
        if limit is None:
            limit = settings.top_picks_limit
        all_fixtures = fixture_repo.get_fixtures_today()
        lead = settings.prematch_min_lead_minutes
        prematch = [f for f in all_fixtures if _is_prematch(f, lead)]
        fixture_ids = [f["id"] for f in prematch]
        predictions = prediction_repo.get_top_picks(fixture_ids, limit=limit)
        return predictions, prematch

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
