"""CLI: Sync player statistics for recent finished fixtures.

Usage:
  python scripts/sync_player_stats_today.py --dry-run
  python scripts/sync_player_stats_today.py --days 2 --limit 10 --execute
  python scripts/sync_player_stats_today.py --fixture 1060362 --execute
  python scripts/sync_player_stats_today.py --league 39 --days 3 --execute
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)


async def main(args: argparse.Namespace) -> None:
    from app.core.logger import setup_logger
    setup_logger()

    from app.data.local.duckdb_client import get_local_db, init_schema
    conn = get_local_db()
    init_schema(conn)

    dry_run = not args.execute
    date = (
        datetime.now(timezone.utc) - timedelta(days=args.days - 1)
    ).strftime("%Y-%m-%d") if args.days else datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print(f"\n=== sync_player_stats_today ===")
    print(f"Mode:    {'EXECUTE' if args.execute else 'DRY-RUN'}")
    print(f"Date:    {date}")
    print(f"Days:    {args.days}")
    print(f"Limit:   {args.limit}")
    if args.fixture:
        print(f"Fixture: {args.fixture}")
    if args.league:
        print(f"League:  {args.league}")
    print()

    from app.services.player_intelligence_service import sync_player_stats_bulk, sync_fixture_player_stats

    if args.fixture:
        result = await sync_fixture_player_stats(
            conn,
            provider_fixture_id=args.fixture,
            dry_run=dry_run,
        )
        print(f"Fixture {args.fixture}: status={result['status']} rows={result.get('rows', 0)}")
    else:
        result = await sync_player_stats_bulk(
            conn,
            date=date,
            league_id=args.league,
            limit=args.limit,
            max_requests=args.max_requests,
            dry_run=dry_run,
        )
        print(f"Status:           {result['status']}")
        print(f"Fixtures:         {result.get('fixtures_processed', 0)}")
        print(f"Rows written:     {result.get('rows_written', 0)}")
        print(f"API calls used:   {result.get('api_calls', 0)}")
        print(f"Errors:           {result.get('errors', 0)}")

    if dry_run:
        print("\n[dry-run] Sin cambios. Usa --execute para guardar.")
    print()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sync player stats for finished fixtures.")
    p.add_argument("--days", type=int, default=1, help="Days back to look for fixtures")
    p.add_argument("--league", type=int, default=None, help="Filter by league ID")
    p.add_argument("--fixture", type=int, default=None, help="Sync one specific fixture ID")
    p.add_argument("--limit", type=int, default=20, help="Max fixtures to process")
    p.add_argument("--max-requests", type=int, default=30, help="Max API requests")
    p.add_argument("--execute", action="store_true", help="Actually write to DB")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
