"""Filter and rank prediction candidates before persisting them."""

import logging
from app.model.predictors.odds_predictor import PredictionCandidate

logger = logging.getLogger(__name__)


class PickFilter:
    """Filters PredictionCandidates by edge, confidence, and max count."""

    def apply(
        self,
        candidates: list[PredictionCandidate],
        min_edge: float,
        min_confidence: float,
        max_picks: int,
    ) -> list[PredictionCandidate]:
        """Return at most ``max_picks`` candidates that meet both thresholds.

        Candidates are sorted by edge (descending) so the highest-value picks
        appear first.

        Args:
            candidates: Output of ``OddsPredictor.calculate``.
            min_edge: Minimum required edge (e.g. 0.05 = 5 %).
            min_confidence: Minimum required model probability (e.g. 0.60 = 60 %).
            max_picks: Hard cap on the number of picks returned.

        Returns:
            Filtered and ranked list of candidates.
        """
        passing = [
            c for c in candidates
            if c.edge >= min_edge and c.confidence_score >= min_confidence
        ]
        passing.sort(key=lambda c: c.edge, reverse=True)
        result = passing[:max_picks]

        logger.info(
            "PickFilter: %d candidatos → %d pasaron (min_edge=%.2f, min_conf=%.2f, max=%d)",
            len(candidates), len(result), min_edge, min_confidence, max_picks,
        )
        return result
