#!/usr/bin/env python
"""Phase 13: Build strategy learning profiles from resolved picks + CLV data.

Usage:
    python scripts/build_strategy_learning.py --days 30 --dry-run
    python scripts/build_strategy_learning.py --days 30 --execute
    python scripts/build_strategy_learning.py --market OU25 --execute
    python scripts/build_strategy_learning.py --league 39 --days 60 --execute
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.logger import setup_logger

logger = logging.getLogger(__name__)


def main(args: argparse.Namespace) -> None:
    if not settings.strategy_learning_enabled and not args.force:
        print("[warn] STRATEGY_LEARNING_ENABLED=false — use --force to override")
        return

    from app.data.local.duckdb_client import get_local_db, init_schema
    conn = get_local_db()
    init_schema(conn)

    from app.services.strategy_learning_service import run_strategy_learning

    dry_run = not args.execute
    if dry_run:
        print(f"[DRY-RUN] would process last {args.days} days")
    else:
        print(f"[EXECUTE] processing last {args.days} days")

    result = run_strategy_learning(
        conn,
        days=args.days,
        dry_run=dry_run,
        market_key=args.market or None,
        league_id=args.league,
        min_sample=args.min_sample,
    )

    print(f"\n=== Strategy Learning Build ===")
    print(f"Run key:      {result['run_key']}")
    print(f"Days:         {result['days']}")
    print(f"Dry-run:      {result['dry_run']}")
    print(f"Annotations:  {result['annotations']}")
    print(f"Profiles:     {result['profiles']}")
    print(f"Adjustments:  {result['adjustments']}")
    print(f"Elapsed:      {result['elapsed_s']}s")

    if result.get("best_key"):
        print(f"\nBest strategy:  {result['best_key']}")
    if result.get("worst_key"):
        print(f"Worst strategy: {result['worst_key']}")

    if dry_run:
        print("\n  Nothing written. Add --execute to persist.")
    else:
        print("\n  Profiles saved to DuckDB.")
        print("  Next: python scripts/audit_strategy_learning.py")
        print("        python scripts/report_strategies.py --best")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build CLV learning strategy profiles")
    p.add_argument("--days", type=int, default=30, help="Days of pick history to process")
    p.add_argument("--market", default="", help="Filter by market key (e.g. OU25, 1X2, BTTS)")
    p.add_argument("--league", type=int, default=None, help="Filter by league_id")
    p.add_argument("--min-sample", type=int, default=None, dest="min_sample",
                   help="Minimum sample size to output a profile")
    p.add_argument("--execute", action="store_true", help="Persist to DuckDB (default: dry-run)")
    p.add_argument("--dry-run", action="store_true", help="Dry-run (default behavior)")
    p.add_argument("--force", action="store_true",
                   help="Run even if STRATEGY_LEARNING_ENABLED=false")
    return p.parse_args()


if __name__ == "__main__":
    setup_logger()
    main(parse_args())
