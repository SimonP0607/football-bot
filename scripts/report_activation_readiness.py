#!/usr/bin/env python
"""Phase 15: Report activation readiness per module.

Usage:
    python scripts/report_activation_readiness.py
    python scripts/report_activation_readiness.py --json
"""
import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logger import setup_logger

logger = logging.getLogger(__name__)

_MODULES = [
    "strategy_learning",
    "bankroll",
    "market_clv",
    "parlay",
    "live_monitoring",
]

_FLAG_MAP = {
    "strategy_learning": "STRATEGY_LEARNING_ENABLED",
    "bankroll":          "BANKROLL_ENGINE_ENABLED",
    "market_clv":        "MARKET_INTELLIGENCE_ENABLED",
    "parlay":            "PARLAY_ENGINE_ENABLED",
    "live_monitoring":   "LIVE_TRACKING_ENABLED",
}

_REC_ORDER = {
    "DO_NOT_ACTIVATE":          0,
    "OBSERVE_MORE":             1,
    "SAFE_TO_TEST_SHADOW":      2,
    "SAFE_TO_TEST_ASSIST":      3,
    "SAFE_TO_USE_FOR_SELECTION": 4,
}

_REC_ICON = {
    "DO_NOT_ACTIVATE":           "✗ ",
    "OBSERVE_MORE":              "◎ ",
    "SAFE_TO_TEST_SHADOW":       "◑ ",
    "SAFE_TO_TEST_ASSIST":       "◕ ",
    "SAFE_TO_USE_FOR_SELECTION": "● ",
}


def _bar(passed: int, total: int) -> str:
    if total == 0:
        return "n/a"
    filled = round(passed / total * 10)
    return "[" + "█" * filled + "░" * (10 - filled) + f"]  {passed}/{total}"


def _section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print('─' * 60)


def main(args: argparse.Namespace) -> None:
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.data.local.model_governance_repo import get_activation_recommendation

    conn = get_local_db()
    init_schema(conn)

    readiness = []
    for module in _MODULES:
        rec = get_activation_recommendation(conn, module)
        flag = _FLAG_MAP.get(module, "?")
        if rec:
            entry = {
                "module": module,
                "flag": flag,
                "recommendation": rec.get("recommendation", "DO_NOT_ACTIVATE"),
                "sample_size": rec.get("sample_size", 0),
                "gates_passed": rec.get("gates_passed", 0),
                "gates_total": rec.get("gates_total", 0),
                "roi": rec.get("roi"),
                "lift_vs_baseline": rec.get("lift_vs_baseline"),
                "blocking_reason": rec.get("blocking_reason"),
                "notes": rec.get("notes"),
                "valid_until": str(rec.get("valid_until") or ""),
            }
        else:
            entry = {
                "module": module,
                "flag": flag,
                "recommendation": "OBSERVE_MORE",
                "sample_size": 0,
                "gates_passed": 0,
                "gates_total": 0,
                "roi": None,
                "lift_vs_baseline": None,
                "blocking_reason": "No data — run run_experiment_lab.py --execute",
                "notes": None,
                "valid_until": "",
            }
        readiness.append(entry)

    if args.json:
        print(json.dumps(readiness, indent=2, default=str))
        return

    print(f"\n{'=' * 60}")
    print("  Activation Readiness Report — Phase 15")
    print(f"{'=' * 60}")

    for entry in sorted(readiness, key=lambda e: _REC_ORDER.get(e["recommendation"], 0), reverse=True):
        module = entry["module"]
        rec    = entry["recommendation"]
        icon   = _REC_ICON.get(rec, "  ")
        flag   = entry["flag"]
        n      = entry["sample_size"]
        gp     = entry["gates_passed"]
        gt     = entry["gates_total"]
        roi    = entry["roi"]
        lift   = entry["lift_vs_baseline"]
        blocking = entry.get("blocking_reason") or ""

        _section(f"{icon}{module.upper()}")
        print(f"  Feature flag  : {flag}")
        print(f"  Recommendation: {rec}")
        print(f"  Sample size   : {n}")
        print(f"  Gates         : {_bar(gp, gt)}")
        roi_str  = f"{roi * 100:+.1f}%" if roi is not None else "n/a"
        lift_str = f"{lift * 100:+.1f}%" if lift is not None else "n/a"
        print(f"  ROI / Lift    : {roi_str} / {lift_str}")
        if blocking:
            print(f"  Blocking      : {blocking}")
        if rec in ("SAFE_TO_TEST_SHADOW", "SAFE_TO_TEST_ASSIST", "SAFE_TO_USE_FOR_SELECTION"):
            print(f"\n  To activate (shadow): set {flag}=true in .env")
            print(f"  IMPORTANT: MODEL_GOVERNANCE_AUTO_ACTIVATE must remain false")

    print(f"\n{'=' * 60}")
    print("  NOTE: No module is ever activated automatically.")
    print("  All activations require explicit .env change + restart.")
    print(f"{'=' * 60}\n")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Report Phase 15 activation readiness")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    return p.parse_args()


if __name__ == "__main__":
    setup_logger()
    main(parse_args())
