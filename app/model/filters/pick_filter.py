"""Filter and rank prediction candidates before persisting them."""

import logging
from app.model.predictors.odds_predictor import PredictionCandidate

logger = logging.getLogger(__name__)

# Quality guard constants (structural thresholds, not user-configurable):
# A single-source consensus is unreliable — require at least 2 bookmakers.
_MIN_BOOKMAKERS = 2
# Long-shot picks (odd > 8.0) require a clear consensus probability; below that
# the model is just picking a lottery outcome with no genuine edge signal.
_MAX_ODD_THRESHOLD = 8.0
_HIGH_ODD_MIN_CONF = 0.55


class PickFilter:
    """Filters PredictionCandidates by edge, confidence, and max count."""

    def apply(
        self,
        candidates: list[PredictionCandidate],
        min_edge: float,
        min_confidence: float,
        max_picks: int,
    ) -> list[PredictionCandidate]:
        """Return at most ``max_picks`` candidates that meet all thresholds.

        Filters applied in order:
        1. Minimum bookmakers (structural: single-source consensus is unreliable).
        2. Extreme-odds guard (long shots without high confidence are discarded).
        3. Min edge + min confidence (calibration thresholds from config).

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
            arg = c.argument_json or {}

            # ── Quality guard 1: bookmaker count ──────────────────────────────
            bk_count = arg.get("bookmakers_count", 0)
            if bk_count < _MIN_BOOKMAKERS:
                logger.info(
                    "  DESCARTADO fixture=%s %s/%s — bookmakers=%d < %d "
                    "(consenso insuficiente)",
                    c.fixture_id, c.market, c.selection,
                    bk_count, _MIN_BOOKMAKERS,
                )
                continue

            # ── Quality guard 2: extreme odds without confidence ───────────────
            if c.best_odd > _MAX_ODD_THRESHOLD and c.confidence_score < _HIGH_ODD_MIN_CONF:
                logger.info(
                    "  DESCARTADO fixture=%s %s/%s — odd=%.2f > %.1f "
                    "con conf=%.4f < %.2f (longshot sin consenso)",
                    c.fixture_id, c.market, c.selection,
                    c.best_odd, _MAX_ODD_THRESHOLD,
                    c.confidence_score, _HIGH_ODD_MIN_CONF,
                )
                continue

            # ── Calibration thresholds: edge + confidence ─────────────────────
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
