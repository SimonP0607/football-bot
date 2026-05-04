#!/usr/bin/env python
"""Phase 12: Fetch and store odds snapshots for upcoming/recent fixtures.

Usage:
  python scripts/sync_market_odds.py                        # today's fixtures, all leagues
  python scripts/sync_market_odds.py --days 3 --limit 20   # last 3 days, max 20 fixtures
  python scripts/sync_market_odds.py --fixture 1035066      # single fixture
  python scripts/sync_market_odds.py --snapshot-type closing --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_scripts_dir = Path(__file__).resolve().parent
_src_root = _scripts_dir.parent
sys.path.insert(0, str(_src_root))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("sync_market_odds")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sync market odds snapshots to DuckDB")
    p.add_argument("--days", type=int, default=1, help="Look back N days for fixtures (default: 1)")
    p.add_argument("--limit", type=int, default=50, help="Max fixtures to process (default: 50)")
    p.add_argument("--fixture", type=int, help="Sync a single fixture by provider ID")
    p.add_argument(
        "--snapshot-type",
        choices=["opening", "prematch", "near_kickoff", "closing"],
        default="prematch",
        help="Snapshot type label to store (default: prematch)",
    )
    p.add_argument("--bookmaker-id", type=int, help="Filter to a single bookmaker ID")
    p.add_argument("--dry-run", action="store_true", help="Parse and log but do not write")
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return p.parse_args()


def _get_candidate_fixtures(conn, days: int, limit: int) -> list[int]:
    """Return provider_fixture_ids for upcoming/recent fixtures from local DB."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT provider_fixture_id
            FROM fixtures
            WHERE date >= ?
              AND status_short NOT IN ('FT', 'AET', 'PEN', 'CANC', 'AWD', 'WO')
            ORDER BY date ASC
            LIMIT ?
            """,
            [cutoff, limit],
        ).fetchall()
        return [r[0] for r in rows if r[0]]
    except Exception as exc:
        logger.warning("No se pudo consultar fixtures locales: %s", exc)
        return []


async def main() -> None:
    args = _parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    from app.core.config import settings
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.services.market_intelligence_service import sync_market_odds

    if not settings.market_intelligence_enabled and not args.dry_run:
        print("MARKET_INTELLIGENCE_ENABLED=false — usa --dry-run para probar sin activar.")
        return

    conn = get_local_db()
    init_schema(conn)

    if args.fixture:
        provider_fixture_ids = [args.fixture]
        print(f"Fixture único: {args.fixture}")
    else:
        provider_fixture_ids = _get_candidate_fixtures(conn, args.days, args.limit)
        print(f"Fixtures encontrados: {len(provider_fixture_ids)} (últimos {args.days} día(s))")

    if not provider_fixture_ids:
        print("No hay fixtures para procesar.")
        return

    print(f"Snapshot type: {args.snapshot_type} | Dry-run: {args.dry_run}")

    result = await sync_market_odds(
        conn,
        provider_fixture_ids,
        snapshot_type=args.snapshot_type,
        bookmaker_id=args.bookmaker_id,
        dry_run=args.dry_run,
    )

    print(
        f"\nResultado:"
        f"\n  Fixtures procesados : {result['fixtures']}"
        f"\n  API calls           : {result['api_calls']}"
        f"\n  Filas escritas      : {result['rows_written']}"
        + (" [DRY-RUN]" if args.dry_run else "")
    )


if __name__ == "__main__":
    asyncio.run(main())
