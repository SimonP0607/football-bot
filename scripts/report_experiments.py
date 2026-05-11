#!/usr/bin/env python
"""Phase 15: Report experiment results.

Usage:
    python scripts/report_experiments.py --days 30 --summary
    python scripts/report_experiments.py --days 30 --detail
    python scripts/report_experiments.py --experiment variant_strategy_learning --detail
    python scripts/report_experiments.py --days 30 --json
"""
import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logger import setup_logger

logger = logging.getLogger(__name__)

_REC_LABEL = {
    "DO_NOT_ACTIVATE":           "DO NOT ACTIVATE",
    "OBSERVE_MORE":              "Observe more",
    "SAFE_TO_TEST_SHADOW":       "Safe: shadow test",
    "SAFE_TO_TEST_ASSIST":       "Safe: assist test",
    "SAFE_TO_USE_FOR_SELECTION": "SAFE TO USE",
}


def _section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print('─' * 60)


def _print_summary(results: list[dict]) -> None:
    _section("Experiment Summary")
    if not results:
        print("  No results. Run: python scripts/run_experiment_lab.py --execute")
        return
    for r in results:
        key = r.get("experiment_key", "?")
        rec = r.get("recommendation", "DO_NOT_ACTIVATE")
        label = _REC_LABEL.get(rec, rec)
        n    = r.get("sample_size", 0)
        roi  = r.get("roi")
        lift = r.get("lift_vs_baseline")
        gp   = r.get("gates_passed", 0)
        gt   = r.get("gates_total", 0)
        roi_str  = f"{roi * 100:+.1f}%" if roi is not None else "n/a"
        lift_str = f"{lift * 100:+.1f}%" if lift is not None else "n/a"
        gates_str = f"{gp}/{gt}" if gt else "—"
        print(f"  {key:35s}  n={n:5d}  roi={roi_str:7s}  lift={lift_str:7s}  "
              f"gates={gates_str:5s}  {label}")


def _print_detail(r: dict) -> None:
    key = r.get("experiment_key", "?")
    _section(f"Detail: {key}")
    print(f"  Module        : {r.get('module', '?')}")
    print(f"  Days window   : {r.get('days_window', '?')}")
    print(f"  Sample        : {r.get('sample_size', 0)}")
    print(f"  W/L/V         : {r.get('wins', 0)}/{r.get('losses', 0)}/{r.get('voids', 0)}")
    hr = r.get("hit_rate")
    print(f"  Hit rate      : {hr * 100:.1f}%" if hr else "  Hit rate      : n/a")
    roi = r.get("roi")
    print(f"  ROI           : {roi * 100:+.1f}%" if roi is not None else "  ROI           : n/a")
    lift = r.get("lift_vs_baseline")
    b_roi = r.get("baseline_roi")
    print(f"  Baseline ROI  : {b_roi * 100:+.1f}%" if b_roi is not None else "  Baseline ROI  : n/a")
    print(f"  Lift          : {lift * 100:+.1f}%" if lift is not None else "  Lift          : n/a")
    clv = r.get("avg_clv_percent")
    print(f"  Avg CLV       : {clv:.3f}" if clv is not None else "  Avg CLV       : n/a")
    dd = r.get("max_drawdown")
    print(f"  Max drawdown  : {dd:.2f}u" if dd is not None else "  Max drawdown  : n/a")
    ci_lo = r.get("confidence_interval_low")
    ci_hi = r.get("confidence_interval_high")
    if ci_lo is not None and ci_hi is not None:
        print(f"  CI (90%)      : [{ci_lo:.3f}, {ci_hi:.3f}]")
    gp = r.get("gates_passed", 0)
    gt = r.get("gates_total", 0)
    print(f"  Gates         : {gp}/{gt}")
    print(f"  Recommendation: {r.get('recommendation', '?')}")
    blocking = r.get("blocking_reason") or r.get("notes", {})
    if isinstance(blocking, dict):
        blocking = blocking.get("blocking_reason", "")
    if blocking:
        print(f"  Blocking      : {blocking}")


def main(args: argparse.Namespace) -> None:
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.data.local.model_governance_repo import get_experiment_results

    conn = get_local_db()
    init_schema(conn)

    days = args.days
    results = get_experiment_results(conn, experiment_key=args.experiment, days_window=days)

    if args.json:
        print(json.dumps(results, indent=2, default=str))
        return

    print(f"\n{'=' * 60}")
    print(f"  Experiment Report — {days}d window")
    print(f"{'=' * 60}")

    if args.summary or not args.detail:
        _print_summary(results)

    if args.detail:
        if args.experiment:
            target = [r for r in results if r.get("experiment_key") == args.experiment]
        else:
            target = results
        for r in target:
            _print_detail(r)

    print(f"\n{'=' * 60}\n")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Report Phase 15 experiment results")
    p.add_argument("--days", type=int, default=30, help="Days window (default: 30)")
    p.add_argument("--experiment", type=str, default=None,
                   help="Show only this experiment key")
    p.add_argument("--summary", action="store_true", help="Print summary table")
    p.add_argument("--detail", action="store_true", help="Print detail per experiment")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    return p.parse_args()


if __name__ == "__main__":
    setup_logger()
    main(parse_args())
