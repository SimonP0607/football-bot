"""CLI: Run the Smart Parlay Engine for a given date.

Usage:
  python scripts/run_parlay_engine.py                  # dry-run, today
  python scripts/run_parlay_engine.py --execute        # save to DuckDB
  python scripts/run_parlay_engine.py --date 2026-05-01
  python scripts/run_parlay_engine.py --legs 2         # 2-leg only
  python scripts/run_parlay_engine.py --legs 3 4       # 3 and 4-leg
  python scripts/run_parlay_engine.py --all            # 2, 3, and 4-leg
  python scripts/run_parlay_engine.py --include-observed
  python scripts/run_parlay_engine.py --limit 10
  python scripts/run_parlay_engine.py --verbose
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Smart Parlay Engine")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Preview only, do not write to DuckDB (default)")
    parser.add_argument("--execute", action="store_true",
                        help="Write results to DuckDB")
    parser.add_argument("--date", default=None,
                        help="Target date YYYY-MM-DD (default: today UTC)")
    parser.add_argument("--legs", nargs="+", type=int, choices=[2, 3, 4],
                        help="Leg counts to generate (e.g. --legs 2 3)")
    parser.add_argument("--all", dest="all_legs", action="store_true",
                        help="Generate 2, 3, and 4-leg parlays (default)")
    parser.add_argument("--include-observed", action="store_true",
                        help="Include picks with status=observed")
    parser.add_argument("--limit", type=int, default=20,
                        help="Max parlays to save per run (default: 20)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print each recommended parlay")
    args = parser.parse_args()

    dry_run = not args.execute
    n_legs_list = args.legs if args.legs else [2, 3, 4]

    mode = "DRY-RUN" if dry_run else "EXECUTE"
    print(f"\n{'='*60}")
    print(f"  Smart Parlay Engine — {mode}")
    print(f"  Date: {args.date or 'today (UTC)'}")
    print(f"  Legs: {n_legs_list}")
    print(f"  Limit: {args.limit}")
    print(f"  Include observed: {args.include_observed}")
    print(f"{'='*60}\n")

    from app.services.parlay_engine_service import run_parlay_engine
    stats = run_parlay_engine(
        date_str=args.date,
        n_legs_list=n_legs_list,
        include_observed=args.include_observed,
        dry_run=dry_run,
        execute=args.execute,
        limit=args.limit,
        verbose=args.verbose,
    )

    print(f"Picks loaded:        {stats['picks_loaded']}")
    print(f"Picks eligible:      {stats['picks_eligible']}")
    print(f"Parlays generated:   {stats['parlays_generated']}")
    print(f"  Recommended:       {stats['parlays_recommended']}")
    print(f"  Observed:          {stats['parlays_observed']}")
    print(f"  Rejected:          {stats['parlays_rejected']}")
    print(f"Parlays saved:       {stats['parlays_saved']}")

    if stats.get("errors"):
        print(f"\nErrors ({len(stats['errors'])}):")
        for e in stats["errors"]:
            print(f"  - {e}")

    if dry_run and stats["parlays_recommended"] > 0:
        print(f"\nRun with --execute to save {stats['parlays_recommended']} recommended parlays.")

    if args.verbose and not dry_run:
        print("\nTop recommended parlays were saved to DuckDB.")
        print("Use: python scripts/report_parlays.py --today")


if __name__ == "__main__":
    main()
