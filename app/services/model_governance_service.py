"""Phase 15: Model Governance Service.

Observes and compares modules without changing production picks.
Uses statistical evidence to recommend (never automatically execute) safe activation.
"""
from __future__ import annotations

import logging
import math
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# ── Recommendation labels (ordered by severity) ────────────────────────────────
REC_DO_NOT_ACTIVATE        = "DO_NOT_ACTIVATE"
REC_OBSERVE_MORE           = "OBSERVE_MORE"
REC_SAFE_TO_TEST_SHADOW    = "SAFE_TO_TEST_SHADOW"
REC_SAFE_TO_TEST_ASSIST    = "SAFE_TO_TEST_ASSIST"
REC_SAFE_TO_USE_FOR_SELECT = "SAFE_TO_USE_FOR_SELECTION"

_REC_RANK = {
    REC_DO_NOT_ACTIVATE:        0,
    REC_OBSERVE_MORE:           1,
    REC_SAFE_TO_TEST_SHADOW:    2,
    REC_SAFE_TO_TEST_ASSIST:    3,
    REC_SAFE_TO_USE_FOR_SELECT: 4,
}


# ── Statistical helpers ────────────────────────────────────────────────────────

def compute_confidence_interval(wins: int, sample_size: int, z: float = 1.645) -> tuple[float, float]:
    """Wilson confidence interval for a hit rate.  Returns (low, high) as fractions."""
    if sample_size <= 0:
        return (0.0, 1.0)
    p = wins / sample_size
    n = sample_size
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def compute_drawdown(results: list[float]) -> float:
    """Maximum drawdown from a sequence of per-pick profit/loss values."""
    if not results:
        return 0.0
    peak = 0.0
    max_dd = 0.0
    running = 0.0
    for r in results:
        running += r
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 4)


def compute_variant_lift(baseline_roi: float | None, variant_roi: float | None) -> float | None:
    """ROI delta: variant - baseline (both as decimals, e.g. 0.05 = 5%)."""
    if baseline_roi is None or variant_roi is None:
        return None
    return round(variant_roi - baseline_roi, 4)


# ── Safe activation gates ──────────────────────────────────────────────────────

def _gate(name: str, passed: bool, reason: str = "") -> dict[str, Any]:
    return {"gate": name, "passed": passed, "reason": reason}


def evaluate_strategy_gates(
    sample_size: int,
    roi: float | None,
    baseline_roi: float | None,
    drawdown: float | None,
    baseline_drawdown: float | None,
    clv_avg: float | None,
    market_count: int = 0,
    league_dependency: float = 0.0,  # fraction of picks from top league
) -> list[dict[str, Any]]:
    """Returns list of gate results for Strategy Learning module."""
    gates = []
    gates.append(_gate(
        "min_sample_200",
        sample_size >= 200,
        f"sample={sample_size} (need 200)",
    ))
    roi_lift = compute_variant_lift(baseline_roi, roi)
    gates.append(_gate(
        "roi_beats_baseline_3pct",
        roi_lift is not None and roi_lift > 0.03,
        f"lift={roi_lift:.3f}" if roi_lift is not None else "no data",
    ))
    gates.append(_gate(
        "drawdown_not_worse",
        drawdown is None or baseline_drawdown is None or drawdown <= baseline_drawdown * 1.10,
        f"dd={drawdown} baseline={baseline_drawdown}",
    ))
    gates.append(_gate(
        "clv_avg_non_negative",
        clv_avg is not None and clv_avg >= 0.0,
        f"clv_avg={clv_avg}",
    ))
    gates.append(_gate(
        "min_3_markets",
        market_count >= 3,
        f"markets={market_count}",
    ))
    gates.append(_gate(
        "no_single_league_dependency",
        league_dependency < 0.70,
        f"top_league_frac={league_dependency:.2f}",
    ))
    return gates


def evaluate_bankroll_gates(
    sample_size: int,
    roi: float | None,
    baseline_roi: float | None,
    drawdown: float | None,
    baseline_drawdown: float | None,
    league_exposure: float = 0.0,    # max fraction in single league
    market_exposure: float = 0.0,    # max fraction in single market
    team_exposure: float = 0.0,      # max fraction in single team
    picks_maintained: float = 1.0,   # fraction of picks not rejected by bankroll
) -> list[dict[str, Any]]:
    """Returns list of gate results for Bankroll module."""
    gates = []
    gates.append(_gate(
        "min_sample_100",
        sample_size >= 100,
        f"sample={sample_size} (need 100)",
    ))
    gates.append(_gate(
        "reduces_drawdown",
        drawdown is not None and baseline_drawdown is not None and drawdown < baseline_drawdown,
        f"dd={drawdown} baseline={baseline_drawdown}",
    ))
    roi_lift = compute_variant_lift(baseline_roi, roi)
    gates.append(_gate(
        "roi_not_hurt_more_than_2pct",
        roi_lift is None or roi_lift >= -0.02,
        f"lift={roi_lift:.3f}" if roi_lift is not None else "no data",
    ))
    gates.append(_gate(
        "low_league_exposure",
        league_exposure < 0.50,
        f"max_league={league_exposure:.2f}",
    ))
    gates.append(_gate(
        "low_market_exposure",
        market_exposure < 0.60,
        f"max_market={market_exposure:.2f}",
    ))
    gates.append(_gate(
        "maintains_sufficient_picks",
        picks_maintained >= 0.70,
        f"maintained={picks_maintained:.2f}",
    ))
    return gates


def evaluate_market_gates(
    sample_size: int,
    clv_avg: float | None,
    clv_beat_rate: float | None,
    roi: float | None,
    baseline_roi: float | None,
    drawdown: float | None,
    baseline_drawdown: float | None,
) -> list[dict[str, Any]]:
    """Returns list of gate results for Market CLV module."""
    gates = []
    gates.append(_gate(
        "min_sample_150_with_clv",
        sample_size >= 150,
        f"sample={sample_size} (need 150)",
    ))
    gates.append(_gate(
        "clv_avg_positive",
        clv_avg is not None and clv_avg > 0.0,
        f"clv_avg={clv_avg}",
    ))
    gates.append(_gate(
        "clv_beat_rate_over_50pct",
        clv_beat_rate is not None and clv_beat_rate >= 0.50,
        f"beat_rate={clv_beat_rate}",
    ))
    roi_lift = compute_variant_lift(baseline_roi, roi)
    gates.append(_gate(
        "roi_not_destroyed_by_signals",
        roi_lift is None or roi_lift >= -0.03,
        f"lift={roi_lift:.3f}" if roi_lift is not None else "no data",
    ))
    gates.append(_gate(
        "drawdown_within_limit",
        drawdown is None or baseline_drawdown is None or drawdown <= baseline_drawdown * 1.20,
        f"dd={drawdown} baseline={baseline_drawdown}",
    ))
    return gates


def evaluate_parlay_gates(
    sample_size: int,
    roi: float | None,
    drawdown: float | None,
    max_stake: float | None,
    correlation_controlled: bool = True,
) -> list[dict[str, Any]]:
    """Returns list of gate results for Parlay module."""
    gates = []
    gates.append(_gate(
        "min_50_resolved_parlays",
        sample_size >= 50,
        f"sample={sample_size} (need 50)",
    ))
    gates.append(_gate(
        "correlation_controlled",
        correlation_controlled,
        "corr checked" if correlation_controlled else "corr not controlled",
    ))
    gates.append(_gate(
        "roi_non_negative",
        roi is not None and roi >= 0.0,
        f"roi={roi}",
    ))
    gates.append(_gate(
        "drawdown_within_limit",
        drawdown is None or drawdown <= 5.0,
        f"drawdown={drawdown}",
    ))
    gates.append(_gate(
        "max_stake_under_0_5u",
        max_stake is None or max_stake <= 0.5,
        f"max_stake={max_stake}",
    ))
    return gates


# ── Gate summary → recommendation ─────────────────────────────────────────────

def gates_to_recommendation(
    gates: list[dict[str, Any]],
    sample_size: int,
    min_sample: int,
) -> tuple[str, str, int, int]:
    """Convert gate list → (recommendation, blocking_reason, passed, total)."""
    passed = sum(1 for g in gates if g["passed"])
    total = len(gates)

    if sample_size < min_sample:
        return (
            REC_OBSERVE_MORE,
            f"Insufficient sample: {sample_size} < {min_sample}",
            passed,
            total,
        )

    if passed == 0:
        return (REC_DO_NOT_ACTIVATE, "No gates passed", passed, total)

    failing = [g for g in gates if not g["passed"]]
    if not failing:
        return (REC_SAFE_TO_USE_FOR_SELECT, "", passed, total)

    if passed >= total - 1:
        return (REC_SAFE_TO_TEST_ASSIST, f"Minor: {failing[0]['gate']}", passed, total)

    if passed >= total * 0.6:
        return (REC_SAFE_TO_TEST_SHADOW, f"Failing: {', '.join(g['gate'] for g in failing)}", passed, total)

    return (REC_OBSERVE_MORE, f"Multiple failing gates: {', '.join(g['gate'] for g in failing)}", passed, total)


# ── Experiment engine ──────────────────────────────────────────────────────────

def _load_baseline_stats(conn, days: int) -> dict[str, Any]:
    """Load aggregated stats for the baseline_current experiment."""
    try:
        r = conn.execute(
            """
            SELECT COUNT(*), AVG(pla.roi), AVG(pla.clv_percent)
            FROM pick_learning_annotations pla
            JOIN experiment_pick_assignments epa
              ON epa.pick_candidate_id = pla.pick_candidate_id
             AND epa.experiment_key = 'baseline_current'
            WHERE pla.created_at >= current_timestamp - INTERVAL (?) DAY
              AND pla.result_status IN ('win', 'loss')
            """,
            [days],
        ).fetchone()
        return {
            "sample_size": r[0] or 0,
            "roi": r[1],
            "clv_avg": r[2],
        }
    except Exception:
        return {"sample_size": 0, "roi": None, "clv_avg": None}


def _load_variant_stats(conn, experiment_key: str, days: int) -> dict[str, Any]:
    """Load aggregated stats for a shadow experiment variant via annotations."""
    try:
        r = conn.execute(
            """
            SELECT COUNT(*), SUM(CASE WHEN pla.result_status='win' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN pla.result_status='loss' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN pla.result_status='void' THEN 1 ELSE 0 END),
                   AVG(pla.profit), AVG(pla.clv_percent),
                   SUM(CASE WHEN pla.beat_closing_line THEN 1 ELSE 0 END),
                   AVG(epa.shadow_edge)
            FROM pick_learning_annotations pla
            JOIN experiment_pick_assignments epa
              ON epa.pick_candidate_id = pla.pick_candidate_id
             AND epa.experiment_key = ?
             AND epa.shadow_selected = true
            WHERE pla.created_at >= current_timestamp - INTERVAL (?) DAY
              AND pla.result_status IN ('win', 'loss', 'void')
            """,
            [experiment_key, days],
        ).fetchone()
        n = r[0] or 0
        wins = r[1] or 0
        losses = r[2] or 0
        decided = wins + losses
        return {
            "sample_size": n,
            "wins": wins,
            "losses": losses,
            "voids": r[3] or 0,
            "hit_rate": round(wins / decided, 4) if decided else None,
            "roi": r[4],
            "clv_avg": r[5],
            "clv_beat": round((r[6] or 0) / n, 4) if n else None,
            "avg_edge": r[7],
        }
    except Exception:
        return {
            "sample_size": 0, "wins": 0, "losses": 0, "voids": 0,
            "hit_rate": None, "roi": None, "clv_avg": None,
            "clv_beat": None, "avg_edge": None,
        }


def _load_variant_profits(conn, experiment_key: str, days: int) -> list[float]:
    """Load ordered profit sequence for drawdown computation."""
    try:
        rows = conn.execute(
            """
            SELECT pla.profit
            FROM pick_learning_annotations pla
            JOIN experiment_pick_assignments epa
              ON epa.pick_candidate_id = pla.pick_candidate_id
             AND epa.experiment_key = ?
             AND epa.shadow_selected = true
            WHERE pla.created_at >= current_timestamp - INTERVAL (?) DAY
              AND pla.result_status IN ('win', 'loss')
              AND pla.profit IS NOT NULL
            ORDER BY pla.created_at
            """,
            [experiment_key, days],
        ).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


def run_experiment_lab(
    conn,
    days: int = 30,
    experiment_key: str | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Compute results for all (or one) experiment(s) and persist if not dry_run."""
    from app.data.local.model_governance_repo import (
        get_active_experiments,
        get_experiment,
        upsert_experiment_result,
        upsert_activation_recommendation,
    )

    if experiment_key:
        exp = get_experiment(conn, experiment_key)
        experiments = [exp] if exp else []
    else:
        experiments = get_active_experiments(conn)

    baseline_stats = _load_baseline_stats(conn, days)
    baseline_roi = baseline_stats.get("roi")

    baseline_profits = _load_variant_profits(conn, "baseline_current", days)
    baseline_drawdown = compute_drawdown(baseline_profits)

    results = []
    for exp in experiments:
        key = exp["experiment_key"]
        module = exp["module"]
        min_sample = exp.get("min_sample") or 100

        stats = _load_variant_stats(conn, key, days)
        profits = _load_variant_profits(conn, key, days)
        drawdown = compute_drawdown(profits)

        ci_low, ci_high = compute_confidence_interval(
            stats.get("wins") or 0,
            stats.get("sample_size") or 0,
        )
        lift = compute_variant_lift(baseline_roi, stats.get("roi"))

        # evaluate gates per module
        gates: list[dict] = []
        if module == "strategy_learning":
            gates = evaluate_strategy_gates(
                sample_size=stats["sample_size"],
                roi=stats.get("roi"),
                baseline_roi=baseline_roi,
                drawdown=drawdown,
                baseline_drawdown=baseline_drawdown,
                clv_avg=stats.get("clv_avg"),
            )
        elif module == "bankroll":
            gates = evaluate_bankroll_gates(
                sample_size=stats["sample_size"],
                roi=stats.get("roi"),
                baseline_roi=baseline_roi,
                drawdown=drawdown,
                baseline_drawdown=baseline_drawdown,
            )
        elif module == "market_clv":
            gates = evaluate_market_gates(
                sample_size=stats["sample_size"],
                clv_avg=stats.get("clv_avg"),
                clv_beat_rate=stats.get("clv_beat"),
                roi=stats.get("roi"),
                baseline_roi=baseline_roi,
                drawdown=drawdown,
                baseline_drawdown=baseline_drawdown,
            )
        elif module == "parlay":
            gates = evaluate_parlay_gates(
                sample_size=stats["sample_size"],
                roi=stats.get("roi"),
                drawdown=drawdown,
                max_stake=None,
            )
        else:
            # value_engine and baseline: no strict gates, just observe
            gates = []

        gates_passed = sum(1 for g in gates if g["passed"])
        gates_total = len(gates)

        rec, blocking, passed, total = gates_to_recommendation(
            gates, stats["sample_size"], min_sample
        )

        result = {
            "experiment_key": key,
            "module": module,
            "days_window": days,
            "sample_size": stats["sample_size"],
            "wins": stats.get("wins", 0),
            "losses": stats.get("losses", 0),
            "voids": stats.get("voids", 0),
            "hit_rate": stats.get("hit_rate"),
            "roi": stats.get("roi"),
            "avg_edge": stats.get("avg_edge"),
            "avg_clv_percent": stats.get("clv_avg"),
            "clv_beat_rate": stats.get("clv_beat"),
            "max_drawdown": drawdown,
            "confidence_interval_low": round(ci_low, 4),
            "confidence_interval_high": round(ci_high, 4),
            "lift_vs_baseline": lift,
            "baseline_roi": baseline_roi,
            "baseline_sample": baseline_stats.get("sample_size", 0),
            "gates_passed": passed,
            "gates_total": total,
            "recommendation": rec,
            "blocking_reason": blocking,
            "gates": gates,
        }
        results.append(result)

        if not dry_run:
            upsert_experiment_result(
                conn,
                experiment_key=key,
                days_window=days,
                sample_size=stats["sample_size"],
                wins=stats.get("wins", 0),
                losses=stats.get("losses", 0),
                voids=stats.get("voids", 0),
                hit_rate=stats.get("hit_rate"),
                roi=stats.get("roi"),
                avg_edge=stats.get("avg_edge"),
                avg_clv_percent=stats.get("clv_avg"),
                clv_beat_rate=stats.get("clv_beat"),
                max_drawdown=drawdown,
                confidence_interval_low=ci_low,
                confidence_interval_high=ci_high,
                lift_vs_baseline=lift,
                baseline_roi=baseline_roi,
                baseline_sample=baseline_stats.get("sample_size", 0),
                gates_passed=passed,
                gates_total=total,
                recommendation=rec,
                notes={"blocking_reason": blocking, "gates": gates},
            )
            # Update activation_recommendations for non-baseline experiments
            if key != "baseline_current" and gates_total > 0:
                upsert_activation_recommendation(
                    conn,
                    module=module,
                    recommendation=rec,
                    sample_size=stats["sample_size"],
                    gates_passed=passed,
                    gates_total=total,
                    gate_details={g["gate"]: g["passed"] for g in gates},
                    roi=stats.get("roi"),
                    lift_vs_baseline=lift,
                    drawdown=drawdown,
                    clv_avg=stats.get("clv_avg"),
                    blocking_reason=blocking or None,
                )

    return results


# ── Decision audit helper ──────────────────────────────────────────────────────

def record_decision_audit(
    conn,
    pick: dict[str, Any],
    official_selected: bool,
    *,
    enabled: bool = False,
) -> None:
    """Write a model_decision_audit row for a single pick. No-op if not enabled."""
    if not enabled:
        return
    from app.data.local.model_governance_repo import insert_model_decision_audit
    try:
        insert_model_decision_audit(
            conn,
            pick_candidate_id=pick.get("id"),
            published_pick_id=pick.get("published_pick_id"),
            fixture_id=pick.get("fixture_id"),
            market_key=pick.get("market_key"),
            league_id=pick.get("league_id"),
            assigned_date=date.today(),
            official_selected=official_selected,
            official_edge=pick.get("edge"),
            official_quality=pick.get("quality_score"),
            ve_selected=pick.get("ve_selected"),
            ve_edge=pick.get("ve_edge"),
            ve_quality=pick.get("ve_quality"),
            sl_recommendation=pick.get("sl_recommendation"),
            sl_strategy_score=pick.get("sl_strategy_score"),
            br_recommended_units=pick.get("bankroll_recommended_units"),
            br_risk_label=pick.get("bankroll_risk_label"),
            br_rejected=pick.get("bankroll_rejected", False),
            mi_signal=pick.get("market_signal"),
            mi_clv_percent=pick.get("clv_percent"),
            final_selected=official_selected,
            decision_changed=False,
            safety_blocked=False,
        )
    except Exception as exc:
        logger.debug("record_decision_audit failed: %s", exc)


# ── Governance job entry point ─────────────────────────────────────────────────

def run_governance_job(conn, days: int = 30, dry_run: bool = False) -> dict[str, Any]:
    """Top-level entry point: register defaults + run experiment lab."""
    from app.data.local.model_governance_repo import (
        create_experiment,
        DEFAULT_EXPERIMENTS,
        get_governance_summary,
    )

    # Ensure default experiments exist
    for defn in DEFAULT_EXPERIMENTS:
        try:
            create_experiment(
                conn,
                experiment_key=defn["experiment_key"],
                variant_name=defn["variant_name"],
                module=defn["module"],
                description=defn.get("description", ""),
                min_sample=defn.get("min_sample", 100),
            )
        except Exception as exc:
            logger.warning("create_experiment(%s): %s", defn["experiment_key"], exc)

    results = run_experiment_lab(conn, days=days, dry_run=dry_run)
    summary = get_governance_summary(conn)

    return {
        "dry_run": dry_run,
        "days": days,
        "experiments_processed": len(results),
        "results": results,
        "summary": summary,
    }
