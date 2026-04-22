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

        Logs every rejection with the exact failing value so calibration is
        fully auditable without needing to re-run at DEBUG level.

        Args:
            candidates: Output of ``OddsPredictor.calculate`` (enriched by
                        MatchContextScorer).
            min_edge: Minimum required edge (model_prob - implied_prob).
            min_confidence: Minimum required model probability.
            max_picks: Hard cap on the number of picks returned.

        Returns:
            Filtered and ranked list of candidates.
        """
        passing: list[PredictionCandidate] = []

        for c in candidates:
            fails_edge = c.edge < min_edge
            fails_conf = c.confidence_score < min_confidence

            if fails_edge or fails_conf:
                if fails_edge and fails_conf:
                    reason = (
                        f"edge={c.edge:.4f} < {min_edge:.2f} "
                        f"y conf={c.confidence_score:.4f} < {min_confidence:.2f}"
                    )
                elif fails_edge:
                    reason = (
                        f"edge={c.edge:.4f} < {min_edge:.2f} "
                        f"(conf={c.confidence_score:.4f} ✓)"
                    )
                else:
                    reason = (
                        f"conf={c.confidence_score:.4f} < {min_confidence:.2f} "
                        f"(edge={c.edge:.4f} ✓)"
                    )
                logger.info(
                    "  DESCARTADO fixture=%s %s/%s — %s | "
                    "model_prob=%.4f implied=%.4f best_odd=%.2f",
                    c.fixture_id, c.market, c.selection, reason,
                    c.model_probability, c.implied_probability, c.best_odd,
                )
            else:
                passing.append(c)

        passing.sort(key=lambda c: c.edge, reverse=True)
        result = passing[:max_picks]

        for c in result:
            logger.info(
                "  ACEPTADO  fixture=%s %s/%s — edge=%.4f conf=%.4f "
                "best_odd=%.2f model_prob=%.4f implied=%.4f",
                c.fixture_id, c.market, c.selection,
                c.edge, c.confidence_score,
                c.best_odd, c.model_probability, c.implied_probability,
            )

        logger.info(
            "PickFilter: %d candidatos → %d pasaron (min_edge=%.2f, min_conf=%.2f, max=%d)",
            len(candidates), len(result), min_edge, min_confidence, max_picks,
        )
        return result
