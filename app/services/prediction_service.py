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
        4. Collect ALL enriched candidates across ALL fixtures.
        5. Apply PickFilter ONCE globally: thresholds + composite ranking + hard cap.
        6. Persist all candidates (is_publishable = True only for the top-cap winners).
        7. Create pending settlement for official picks (idempotent).

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
                "settlement_created": 0,
                "settlement_existing": 0,
            }

        lead = settings.prematch_min_lead_minutes
        prematch_fixtures = [f for f in all_fixtures if _is_prematch(f, lead)]
        discarded_started = len(all_fixtures) - len(prematch_fixtures)
        if discarded_started:
            logger.info(
                "Prematch filter: %d/%d fixtures descartados (iniciados o en <%d min)",
                discarded_started, len(all_fixtures), lead,
            )

        # Phase 1: collect ALL enriched candidates across every fixture
        discarded_no_odds = 0
        all_enriched: list[PredictionCandidate] = []

        for fix in prematch_fixtures:
            fixture_id: int = fix["id"]
            odds_rows = odds_repo.get_odds_for_fixture(fixture_id)
            if not odds_rows:
                logger.debug("fixture_id=%s sin cuotas, omitido", fixture_id)
                discarded_no_odds += 1
                continue

            context = fix.get("context_json") or {}
            candidates = self._predictor.calculate(odds_rows)
            if not candidates:
                continue

            enriched = [self._enrich_candidate(c, context) for c in candidates]
            all_enriched.extend(enriched)

            logger.debug(
                "fixture_id=%s — %d candidatos acumulados (total hasta ahora: %d)",
                fixture_id, len(enriched), len(all_enriched),
            )

        total_candidates = len(all_enriched)
        fixtures_with_odds = len(prematch_fixtures) - discarded_no_odds
        logger.info(
            "Pre-filtro: %d candidatos totales de %d fixtures con cuotas",
            total_candidates, fixtures_with_odds,
        )

        # Phase 2: apply global filter + hard cap ONCE across all candidates
        publishable = self._filter.apply(
            all_enriched,
            min_edge=settings.min_edge,
            min_confidence=settings.min_confidence,
            max_picks=settings.max_daily_picks,
        )
        publishable_set = {(p.fixture_id, p.market, p.selection) for p in publishable}
        logger.info(
            "Cap global: %d picks oficiales de %d candidatos (cap=%d)",
            len(publishable), total_candidates, settings.max_daily_picks,
        )

        # Phase 3: persist ALL candidates — only the top-cap set gets is_publishable=True
        for candidate in all_enriched:
            is_pub = (candidate.fixture_id, candidate.market, candidate.selection) in publishable_set
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

        # Phase 4: create pending settlement for official picks (idempotent)
        # Import here to avoid circular import at module level.
        from app.data.repositories import settlement_repo
        settlement_created = 0
        settlement_existing = 0
        for candidate in publishable:
            saved_id = prediction_repo.get_candidate_id(
                candidate.fixture_id, candidate.market, candidate.selection
            )
            if saved_id:
                row = settlement_repo.create_pending(
                    pick_candidate_id=saved_id,
                    fixture_id=candidate.fixture_id,
                    market_key=candidate.market,
                    selection=candidate.selection,
                    odd_taken=candidate.best_odd,
                )
                if row:
                    settlement_created += 1
                else:
                    settlement_existing += 1

        total_picks = len(publishable)
        summary = {
            "fixtures_fetched": len(all_fixtures),
            "discarded_started": discarded_started,
            "discarded_no_odds": discarded_no_odds,
            "candidates_generated": total_candidates,
            "publishable_saved": total_picks,
            "settlement_created": settlement_created,
            "settlement_existing": settlement_existing,
        }
        logger.info(
            "=== RESUMEN DEL DÍA === fixtures=%d prematch=%d sin_cuotas=%d "
            "candidatos=%d | picks_oficiales=%d (cap=%d) | "
            "settlement_nuevo=%d ya_existia=%d",
            summary["fixtures_fetched"], len(prematch_fixtures),
            discarded_no_odds, total_candidates,
            total_picks, settings.max_daily_picks,
            settlement_created, settlement_existing,
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

        Uses settings.max_daily_picks when limit is None (same cap as official picks).
        fixtures list contains only prematch fixtures.
        """
        if limit is None:
            limit = settings.max_daily_picks
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
