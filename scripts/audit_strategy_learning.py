#!/usr/bin/env python
"""Phase 13: Audit strategy learning state — tables, picks, profiles, recommendations.

Usage:
    python scripts/audit_strategy_learning.py
    python scripts/audit_strategy_learning.py --verbose
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logger import setup_logger

logger = logging.getLogger(__name__)


def _bar(v: float | None, width: int = 20) -> str:
    if v is None:
        return "-" * width
    filled = int(min(1.0, max(0.0, v)) * width)
    return "#" * filled + "." * (width - filled)


def main(args: argparse.Namespace) -> None:
    from app.data.local.duckdb_client import get_local_db, init_schema
    conn = get_local_db()
    init_schema(conn)

    from app.data.local.strategy_learning_repo import (
        audit_strategy_learning, get_learning_summary,
        get_best_strategies, get_weak_strategies, get_strategy_adjustments,
    )
    from app.core.config import settings

    print("\n=== Strategy Learning Audit ===\n")

    # ── Config ────────────────────────────────────────────────────────────────
    print("Config:")
    print(f"  STRATEGY_LEARNING_ENABLED:               {settings.strategy_learning_enabled}")
    print(f"  STRATEGY_LEARNING_USE_FOR_SELECTION:     {settings.strategy_learning_use_for_selection}")
    print(f"  STRATEGY_LEARNING_MIN_SAMPLE:            {settings.strategy_learning_min_sample}")
    print(f"  STRATEGY_LEARNING_SCORE_PROMOTE:         {settings.strategy_learning_score_promote}")
    print(f"  STRATEGY_LEARNING_SCORE_REDUCE:          {settings.strategy_learning_score_reduce}")
    print(f"  STRATEGY_LEARNING_MAX_PENALTY:           {settings.strategy_learning_max_penalty}")
    print(f"  STRATEGY_LEARNING_MAX_BOOST:             {settings.strategy_learning_max_boost}")
    print(f"  SCHEDULER_STRATEGY_LEARNING_ENABLED:     {settings.scheduler_strategy_learning_enabled}")
    print()

    # ── Tables ────────────────────────────────────────────────────────────────
    print("Tables:")
    audit = audit_strategy_learning(conn)
    for tbl, cnt in (audit.get("table_counts") or {}).items():
        mark = "OK" if cnt >= 0 else "ERR"
        print(f"  [{mark}] {tbl}: {cnt} rows")
    print()

    # ── Summary ───────────────────────────────────────────────────────────────
    summary = get_learning_summary(conn)
    if "error" in summary:
        print(f"  [WARN] summary error: {summary['error']}")
    else:
        print("Summary:")
        print(f"  Total picks annotated:   {summary.get('total_annotations', 0)}")
        print(f"  With settlement data:    {summary.get('annotated_with_result', 0)}")
        print(f"  With CLV data:           {summary.get('annotated_with_clv', 0)}")
        print(f"  Strategy profiles:       {summary.get('total_profiles', 0)}")
        print(f"  Pending adjustments:     {summary.get('pending_adjustments', 0)}")
        print()

        rec_counts = summary.get("recommendation_counts") or {}
        if rec_counts:
            print("Recommendation distribution:")
            for rec, cnt in sorted(rec_counts.items()):
                print(f"  {rec:25s} {cnt:5d}")
            print()

    # ── CLV data in DuckDB ────────────────────────────────────────────────────
    try:
        n_clv = conn.execute("SELECT COUNT(*) FROM pick_clv_results").fetchone()[0]
        n_ann = conn.execute("SELECT COUNT(*) FROM pick_learning_annotations").fetchone()[0]
        n_resolved = conn.execute(
            "SELECT COUNT(*) FROM pick_learning_annotations WHERE result_status IN ('win','loss','void')"
        ).fetchone()[0]
        print(f"Pick CLV results (DuckDB):   {n_clv}")
        print(f"Learning annotations:        {n_ann}")
        print(f"Annotations with settlement: {n_resolved}")
        print()
    except Exception as e:
        print(f"  [warn] CLV counts: {e}")

    # ── Best strategies ───────────────────────────────────────────────────────
    best = get_best_strategies(conn, limit=5)
    if best:
        print("Top 5 strategies by score:")
        for p in best:
            sc  = p.get("strategy_score") or 0
            roi = (p.get("roi") or 0) * 100
            clv = p.get("avg_clv_percent") or 0
            n   = p.get("sample_size") or 0
            rec = p.get("recommendation") or "-"
            mkt = p.get("market_key") or "-"
            print(f"  [{sc:5.1f}] {rec:18s} n={n:3d} roi={roi:+.1f}% clv={clv:+.2f}  {mkt}")
        print()

    # ── Weak strategies ───────────────────────────────────────────────────────
    weak = get_weak_strategies(conn, limit=5)
    if weak:
        print("Bottom 5 strategies (lowest score):")
        for p in weak:
            sc  = p.get("strategy_score") or 0
            roi = (p.get("roi") or 0) * 100
            clv = p.get("avg_clv_percent") or 0
            n   = p.get("sample_size") or 0
            rec = p.get("recommendation") or "-"
            mkt = p.get("market_key") or "-"
            print(f"  [{sc:5.1f}] {rec:18s} n={n:3d} roi={roi:+.1f}% clv={clv:+.2f}  {mkt}")
        print()

    # ── Insufficient sample strategies ────────────────────────────────────────
    try:
        n_insuf = conn.execute(
            "SELECT COUNT(*) FROM strategy_profiles WHERE recommendation='insufficient_sample'"
        ).fetchone()[0]
        print(f"Strategies with insufficient sample: {n_insuf}")
    except Exception:
        pass

    # ── Pending adjustments ───────────────────────────────────────────────────
    pending_adjs = get_strategy_adjustments(conn, status="pending")
    if pending_adjs:
        print(f"\nPending adjustments ({len(pending_adjs)}):")
        for adj in pending_adjs[:10]:
            at  = adj.get("adjustment_type") or "-"
            mkt = adj.get("market_key") or "global"
            n   = adj.get("evidence_sample_size") or 0
            roi = (adj.get("evidence_roi") or 0) * 100
            print(f"  [{at:18s}] mkt={mkt:8s} n={n:3d} roi={roi:+.1f}%")
    else:
        print("\nNo pending adjustments.")

    # ── Verbose: label distribution ───────────────────────────────────────────
    if args.verbose:
        label_dist = audit.get("label_dist") or {}
        if label_dist:
            print("\nLearning label distribution:")
            for lbl, cnt in sorted(label_dist.items()):
                print(f"  {lbl:20s} {cnt:5d}")
        market_dist = audit.get("market_dist") or {}
        if market_dist:
            print("\nProfiles by market:")
            for mkt, cnt in sorted(market_dist.items()):
                print(f"  {mkt:10s} {cnt:5d}")

    print("\nRecommendations:")
    print("  1. python scripts/build_strategy_learning.py --days 30 --execute  (if no data yet)")
    print("  2. python scripts/report_strategies.py --best --days 30")
    print("  3. python scripts/report_strategies.py --worst --days 30")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Audit strategy learning state")
    p.add_argument("--verbose", action="store_true", help="Show extra detail")
    return p.parse_args()


if __name__ == "__main__":
    setup_logger()
    main(parse_args())
