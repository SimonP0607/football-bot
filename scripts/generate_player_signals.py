"""CLI: Generate prop signals for upcoming fixtures.

Usage:
  python scripts/generate_player_signals.py --dry-run
  python scripts/generate_player_signals.py --fixture 1060362 --execute
  python scripts/generate_player_signals.py --league 39 --days 2 --execute
  python scripts/generate_player_signals.py --league 39 --limit 5 --execute
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)


def main(args: argparse.Namespace) -> None:
    from app.core.logger import setup_logger
    setup_logger()

    from app.data.local.duckdb_client import get_local_db, init_schema
    conn = get_local_db()
    init_schema(conn)

    dry_run = not args.execute

    print("\n=== generate_player_signals ===")
    print(f"Mode:    {'EXECUTE' if args.execute else 'DRY-RUN'}")
    if args.fixture:
        print(f"Fixture: {args.fixture}")
    if args.league:
        print(f"League:  {args.league}")
    print(f"Days:    {args.days}")
    print(f"Limit:   {args.limit}")
    print()

    from app.services.player_intelligence_service import generate_player_signals

    result = generate_player_signals(
        conn,
        provider_fixture_id=args.fixture,
        league_id=args.league,
        days=args.days,
        limit=args.limit,
        dry_run=dry_run,
    )

    print(f"Status:           {result['status']}")
    print(f"Fixtures:         {result.get('fixtures', 0)}")
    print(f"Signals:          {result.get('signals', 0)}")

    if dry_run:
        print("\n[dry-run] Sin cambios. Usa --execute para guardar.")
    print()
    print("NOTA: Las señales son analíticas. No son recomendaciones de apuesta.")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate prop signals for upcoming fixtures.")
    p.add_argument("--fixture", type=int, default=None, help="Generate for one specific fixture ID")
    p.add_argument("--league", type=int, default=None, help="Filter by league ID")
    p.add_argument("--days", type=int, default=1, help="Days ahead to look for fixtures")
    p.add_argument("--limit", type=int, default=10, help="Max fixtures to process")
    p.add_argument("--execute", action="store_true", help="Actually write to DB")
    return p.parse_args()


if __name__ == "__main__":
    main(parse_args())
