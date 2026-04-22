"""v1 prediction model based on bookmaker-odds consensus.

How it works
------------
For each market (1X2, OU25, BTTS) and each selection within that market:

1. Collect odds from all available bookmakers.
2. Per bookmaker: compute implied probabilities (1/odd) and remove the
   bookmaker's overround (margin) to get *fair probabilities*.
3. Average the fair probabilities across bookmakers → ``model_probability``.
   This represents the market consensus estimate of the true probability.
4. Identify the best available odd (highest) across all bookmakers for that
   selection → ``best_odd``.  ``implied_probability = 1 / best_odd``.
5. ``edge = model_probability - implied_probability``.
   A positive edge means the consensus fair probability is higher than even
   the best-priced bookmaker implies — a potential value signal.
6. ``confidence_score = model_probability`` (how likely the market thinks this
   outcome is, after removing the margin).

This is an honest, data-driven v1 model.  It does NOT use historical match
data or a separate statistical model (Poisson, etc.).  Those belong in v1.1.
"""

import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class PredictionCandidate:
    fixture_id: int
    market: str          # "1X2" | "OU25" | "BTTS"
    selection: str       # "Home" | "Draw" | "Away" | "Over 2.5" | "Yes" …
    model_probability: float
    implied_probability: float
    edge: float
    confidence_score: float
    best_odd: float
    best_bookmaker: str
    argument_json: dict


class OddsPredictor:
    """Calculates PredictionCandidates from a list of odds snapshot rows."""

    def calculate(self, odds_rows: list[dict]) -> list[PredictionCandidate]:
        """Return a PredictionCandidate per selection for each market found.

        Args:
            odds_rows: Rows from ``odds_repo.get_odds_for_fixture``.  Each row
                must have keys: ``fixture_id``, ``bookmaker``, ``market``,
                ``selection``, ``odd`` (numeric).

        Returns:
            Unsorted list of candidates (all selections, all markets).
        """
        if not odds_rows:
            return []

        fixture_id: int = odds_rows[0]["fixture_id"]

        # market → bookmaker → selection → odd
        # DB columns: market_key (not market), bookmaker_name (not bookmaker)
        grouped: dict[str, dict[str, dict[str, float]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        for row in odds_rows:
            market = row.get("market_key") or row.get("market", "")
            bookmaker = row.get("bookmaker_name") or row.get("bookmaker", "Unknown")
            if not market:
                continue
            try:
                grouped[market][bookmaker][row["selection"]] = float(row["odd"])
            except (KeyError, TypeError, ValueError):
                continue

        candidates: list[PredictionCandidate] = []

        for market, bookmakers in grouped.items():
            # Step 1: compute fair probabilities per bookmaker
            fair_by_bookmaker: dict[str, dict[str, float]] = {}
            best_odds: dict[str, float] = defaultdict(float)
            best_bookmaker_for: dict[str, str] = {}

            for bk_name, sel_odds in bookmakers.items():
                if not sel_odds:
                    continue
                implied = {sel: 1.0 / odd for sel, odd in sel_odds.items() if odd > 0}
                overround = sum(implied.values())
                if overround <= 0:
                    continue
                fair = {sel: ip / overround for sel, ip in implied.items()}
                fair_by_bookmaker[bk_name] = fair

                for sel, odd in sel_odds.items():
                    if odd > best_odds[sel]:
                        best_odds[sel] = odd
                        best_bookmaker_for[sel] = bk_name

            if not fair_by_bookmaker:
                logger.debug("market=%s fixture=%s: sin bookmakers válidos, omitido", market, fixture_id)
                continue

            # Step 2: average fair probs across bookmakers per selection
            all_selections = {sel for fp in fair_by_bookmaker.values() for sel in fp}
            for selection in all_selections:
                sel_fair_probs = [
                    fp[selection]
                    for fp in fair_by_bookmaker.values()
                    if selection in fp
                ]
                if not sel_fair_probs:
                    continue

                model_prob = statistics.mean(sel_fair_probs)
                best_odd = best_odds.get(selection, 0.0)
                if best_odd <= 0:
                    continue

                implied_prob = 1.0 / best_odd
                edge = model_prob - implied_prob

                # Build a transparent argument for auditing
                overrounds = []
                for sel_odds in bookmakers.values():
                    if sel_odds:
                        overrounds.append(sum(1.0 / o for o in sel_odds.values() if o > 0))

                argument: dict = {
                    "market": market,
                    "selection": selection,
                    "bookmakers_count": len(fair_by_bookmaker),
                    "fair_probs": {
                        bk: round(fp[selection], 4)
                        for bk, fp in fair_by_bookmaker.items()
                        if selection in fp
                    },
                    "best_odd": best_odd,
                    "best_bookmaker": best_bookmaker_for.get(selection, ""),
                    "avg_overround": round(statistics.mean(overrounds), 4) if overrounds else None,
                    "snapshot_at": datetime.now(timezone.utc).isoformat(),
                    "scope": "prematch",
                }

                candidates.append(
                    PredictionCandidate(
                        fixture_id=fixture_id,
                        market=market,
                        selection=selection,
                        model_probability=round(model_prob, 4),
                        implied_probability=round(implied_prob, 4),
                        edge=round(edge, 4),
                        confidence_score=round(model_prob, 4),
                        best_odd=best_odd,
                        best_bookmaker=best_bookmaker_for.get(selection, ""),
                        argument_json=argument,
                    )
                )

        if candidates:
            edges = [c.edge for c in candidates]
            confs = [c.confidence_score for c in candidates]
            logger.info(
                "OddsPredictor fixture=%s → %d candidatos | "
                "edge [%.4f .. %.4f] | conf [%.4f .. %.4f] | %d mercados",
                fixture_id, len(candidates),
                min(edges), max(edges),
                min(confs), max(confs),
                len(grouped),
            )
        else:
            logger.debug("OddsPredictor fixture=%s → sin candidatos", fixture_id)
        return candidates
