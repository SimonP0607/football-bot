#!/usr/bin/env python
"""Phase 15: Run the experiment lab — compute shadow comparison results.

Usage:
    python scripts/run_experiment_lab.py --days 30 --dry-run
    python scripts/run_experiment_lab.py --days 30 --execute
    python scripts/run_experiment_lab.py --days 30 --experiment variant_strategy_learning --execute
    python scripts/run_experiment_lab.py --all --execute --json
"""
import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logger import setup_logger

logger = logging.getLogger(__name__)

_REC_EMOJI = {
    "DO_NOT_ACTIVATE":          "✗",
    "OBSERVE_MORE":             "◎",
    "SAFE_TO_TEST_SHADOW":      "◑",
    "SAFE_TO_TEST_ASSIST":      "◕",
    "SAFE_TO_USE_FOR_SELECTION": "●",
}


def _print_result(r: dict) -> None:
    key  = r.get("experiment_key", "?")
    rec  = r.get("recommendation", "?")
    emoji = _REC_EMOJI.get(rec, "?")
    n    = r.get("sample_size", 0)
    roi  = r.get("roi")
    lift = r.get("lift_vs_baseline")
    gp   = r.get("gates_passed", 0)
    gt   = r.get("gates_total", 0)

    print(f"\n  {emoji} {key}")
    print(f"     Recommendation : {rec}")
    print(f"     Sample         : {n}")
    roi_str  = f"{roi * 100:+.1f}%" if roi is not None else "n/a"
    lift_str = f"{lift * 100:+.1f}%" if lift is not None else "n/a"
    print(f"     ROI            : {roi_str}  (lift {lift_str})")
    if gt > 0:
        print(f"     Gates passed   : {gp}/{gt}")
        for g in r.get("gates") or []:
            icon = "✓" if g["passed"] else "✗"
            print(f"       {icon}  {g['gate']:35s}  {g.get('reason', '')}")
    blocking = r.get("blocking_reason")
    if blocking:
        print(f"     Blocking       : {blocking}")


def main(args: argparse.Namespace) -> None:
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.services.model_governance_service import run_experiment_lab, run_governance_job

    conn = get_local_db()
    init_schema(conn)

    dry_run = args.dry_run

    if args.all:
        outcome = run_governance_job(conn, days=args.days, dry_run=dry_run)
        results = outcome.get("results", [])
    else:
        results = run_experiment_lab(
            conn,
            days=args.days,
            experiment_key=args.experiment or None,
            dry_run=dry_run,
        )

    if args.json:
        print(json.dumps(results, indent=2, default=str))
        return

    mode = "[DRY RUN]" if dry_run else "[EXECUTE]"
    print(f"\n{'=' * 60}")
    print(f"  Experiment Lab — {args.days}d  {mode}")
    print(f"{'=' * 60}")

    for r in results:
        _print_result(r)

    print(f"\n{'─' * 60}")
    print(f"  Total experiments: {len(results)}")
    print(f"{'─' * 60}\n")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run Phase 15 experiment lab")
    p.add_argument("--days", type=int, default=30, help="Days of history (default: 30)")
    p.add_argument("--experiment", type=str, default=None,
                   help="Specific experiment key to run (default: all)")
    p.add_argument("--all", action="store_true",
                   help="Run full governance job (register defaults + all experiments)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--execute", action="store_true", help="Write results to DB")
    g.add_argument("--dry-run", action="store_true", help="Compute but do not write")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    return p.parse_args()


if __name__ == "__main__":
    setup_logger()
    main(parse_args())
