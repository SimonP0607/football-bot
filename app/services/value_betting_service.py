"""Value betting engine — Phase 5 (calibration only, no live odds).

Applies probability calibration to raw Poisson model outputs and computes:
    p_cal         calibrated probability (from calibration_service)
    fair_odds     1 / p_cal
    w_rel         reliability weight (ECE improvement + sample size)
    risk_score    1 - p_cal  (higher p = lower risk)
    quality_score w_rel * p_cal  (ranking signal for pick selection)

Odds-dependent fields (p_mkt, edge, ev, ev_adj, offered_odds) are NULL in
Phase 5. They will be populated once historical odds are backfilled (Phase 5.5).

Selection engine rules:
    - Evaluates the top selection per market from shadow_service output
    - Applies hard filters: p_cal >= MIN_PCAL, w_rel >= MIN_WREL
    - Selects the single pick with highest quality_score per fixture
    - All others that pass filters: 'rejected_cap'
    - Those that fail filters:     'rejected_filter'
    - Persists all candidates to shadow_value_picks (optional)

Usage:
    from app.services.value_betting_service import evaluate_fixture

    result = evaluate_fixture(
        conn,
        fixture_picks=shadow_result["picks"],
        provider_league_id=39,
        entity_type="club",
        competition_type="domestic_league",
    )
    selected = result["selected"]   # None if no pick passes filters
"""

from __future__ import annotations

import logging
from datetime import date

from app.services.calibration_service import (
    apply_calibrator,
    compute_w_rel,
    get_calibrator_for_pick,
)

logger = logging.getLogger(__name__)

MIN_PCAL = 0.52   # minimum calibrated probability (coin flip + margin)
MIN_WREL = 0.10   # minimum reliability weight
MIN_QUAL = 0.10   # minimum quality_score (= w_rel * p_cal)


# ── Per-pick evaluation ────────────────────────────────────────────────────────


def _evaluate_pick(
    conn,
    *,
    market_key: str,
    selection: str,
    p_raw: float,
    entity_type: str,
    competition_type: str | None,
    provider_league_id: int,
) -> dict:
    """Compute all value metrics for a single market pick.

    Returns dict with p_raw, p_cal, fair_odds, w_rel, risk_score,
    quality_score, and NULL odds-dependent fields.
    Includes internal keys '_calibrated' (bool) and '_meta' (calibrator info).
    """
    params, meta = get_calibrator_for_pick(
        conn,
        market_key=market_key,
        entity_type=entity_type,
        competition_type=competition_type,
        provider_league_id=provider_league_id,
    )

    p_cal         = apply_calibrator(params, p_raw) if params else p_raw
    w_rel         = compute_w_rel(meta)
    fair_odds     = round(1.0 / p_cal, 4) if p_cal > 1e-6 else None
    risk_score    = round(1.0 - p_cal, 4)
    quality_score = round(w_rel * p_cal, 4)

    return {
        "market_key":    market_key,
        "selection":     selection,
        "p_raw":         round(p_raw, 6),
        "p_cal":         round(p_cal, 6),
        "p_mkt":         None,
        "p_adj":         round(p_cal, 6),  # equals p_cal when no market odds
        "fair_odds":     fair_odds,
        "offered_odds":  None,
        "edge":          None,
        "ev":            None,
        "ev_adj":        None,
        "w_rel":         w_rel,
        "risk_score":    risk_score,
        "quality_score": quality_score,
        "_calibrated":   params is not None,
        "_meta":         meta,
    }


# ── Persistence ────────────────────────────────────────────────────────────────


def _persist_candidates(
    conn,
    candidates: list[dict],
    *,
    run_date: str,
    fixture_id: int | None,
    provider_league_id: int,
) -> None:
    """Write all evaluated candidates to shadow_value_picks."""
    for c in candidates:
        conn.execute(
            """
            INSERT INTO shadow_value_picks (
                run_date, fixture_id, provider_league_id,
                market_key, selection,
                p_raw, p_cal, p_mkt, p_adj, fair_odds, offered_odds,
                edge, ev, ev_adj, w_rel, risk_score, quality_score,
                decision_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_date,
                fixture_id,
                provider_league_id,
                c["market_key"],
                c["selection"],
                c["p_raw"],
                c["p_cal"],
                c["p_mkt"],
                c["p_adj"],
                c["fair_odds"],
                c["offered_odds"],
                c["edge"],
                c["ev"],
                c["ev_adj"],
                c["w_rel"],
                c["risk_score"],
                c["quality_score"],
                c.get("decision_status"),
            ],
        )


# ── Public API ─────────────────────────────────────────────────────────────────


def evaluate_fixture(
    conn,
    *,
    fixture_picks: dict,
    provider_league_id: int,
    entity_type: str = "club",
    competition_type: str | None = None,
    run_date: str | None = None,
    fixture_id: int | None = None,
    persist: bool = True,
) -> dict:
    """Evaluate all market picks for a fixture and select the best one.

    Args:
        conn:               DuckDB connection
        fixture_picks:      picks dict from shadow_service predict_fixture["picks"]
                            Format: {market_key: {selection, probability, all_probs}}
        provider_league_id: league ID (for calibrator lookup)
        entity_type:        'club' or 'national_team'
        competition_type:   e.g. 'domestic_league' (for future calibration scope)
        run_date:           ISO date string (default: today)
        fixture_id:         optional reference ID
        persist:            write candidates to shadow_value_picks (default True)

    Returns:
        dict with:
            candidates  list of all evaluated picks (one per market)
            selected    best pick dict, or None if none pass filters
            run_date    ISO date string used for persistence
    """
    run_date = run_date or str(date.today())

    candidates: list[dict] = []
    for market_key, pk in fixture_picks.items():
        pick = _evaluate_pick(
            conn,
            market_key=market_key,
            selection=pk["selection"],
            p_raw=pk["probability"],
            entity_type=entity_type,
            competition_type=competition_type,
            provider_league_id=provider_league_id,
        )
        pick["fixture_id"] = fixture_id
        candidates.append(pick)

    # Hard filters
    passing = [
        c for c in candidates
        if c["p_cal"] >= MIN_PCAL
        and c["w_rel"] >= MIN_WREL
        and c["quality_score"] >= MIN_QUAL
    ]

    # Rank: highest quality_score first
    passing.sort(key=lambda x: x["quality_score"], reverse=True)
    selected = passing[0] if passing else None

    # Assign decision_status
    selected_key = (
        (selected["market_key"], selected["selection"]) if selected else None
    )
    for c in candidates:
        key = (c["market_key"], c["selection"])
        if key == selected_key:
            c["decision_status"] = "selected"
        elif c in passing:
            c["decision_status"] = "rejected_cap"
        else:
            c["decision_status"] = "rejected_filter"

    if persist:
        _persist_candidates(
            conn, candidates,
            run_date=run_date,
            fixture_id=fixture_id,
            provider_league_id=provider_league_id,
        )

    return {
        "candidates": candidates,
        "selected":   selected,
        "run_date":   run_date,
    }
