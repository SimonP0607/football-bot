"""CLI: Settle parlay results against Supabase pick_results.

Usage:
  python scripts/settle_parlays.py              # dry-run
  python scripts/settle_parlays.py --execute    # write settlement to DuckDB
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(description="Settle parlay results")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Preview only, do not write (default)")
    parser.add_argument("--execute", action="store_true",
                        help="Write settlement results to DuckDB")
    args = parser.parse_args()

    dry_run = not args.execute
    mode = "DRY-RUN" if dry_run else "EXECUTE"

    print(f"\n{'='*55}")
    print(f"  Settle Parlays — {mode}")
    print(f"{'='*55}\n")

    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.services.parlay_engine_service import settle_parlays

    conn = get_local_db()
    init_schema(conn)

    stats = settle_parlays(conn, dry_run=dry_run)

    print(f"Parlays checked:     {stats['checked']}")
    print(f"Settled:             {stats['settled']}")
    print(f"Still pending:       {stats['pending']}")
    print(f"Already settled:     {stats['already_settled']}")

    if stats.get("errors"):
        print(f"\nErrors ({len(stats['errors'])}):")
        for e in stats["errors"]:
            print(f"  - {e}")

    if dry_run and stats["settled"] > 0:
        print(f"\nRun with --execute to write {stats['settled']} settlements.")


if __name__ == "__main__":
    main()
