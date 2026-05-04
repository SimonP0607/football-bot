#!/usr/bin/env python
"""Phase 12: Build closing lines from stored odds history snapshots.

Usage:
  python scripts/build_closing_lines.py                # process all fixtures with history
  python scripts/build_closing_lines.py --days 7       # fixtures from last 7 days
  python scripts/build_closing_lines.py --fixture 1035066 --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_scripts_dir = Path(__file__).resolve().parent
_src_root = _scripts_dir.parent
sys.path.insert(0, str(_src_root))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("build_closing_lines")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build closing lines from odds history")
    p.add_argument("--days", type=int, default=7, help="Process fixtures from last N days (default: 7)")
    p.add_argument("--fixture", type=int, help="Process a single fixture by provider ID")
    p.add_argument("--dry-run", action="store_true", help="Compute but do not write")
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return p.parse_args()


def _get_fixtures_with_history(conn, days: int) -> list[int]:
    """Return provider_fixture_ids that have odds snapshot data."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT provider_fixture_id
            FROM market_odds_history
            WHERE created_at >= ?
            ORDER BY provider_fixture_id
            """,
            [cutoff],
        ).fetchall()
        return [r[0] for r in rows if r[0]]
    except Exception as exc:
        logger.warning("No se pudo consultar market_odds_history: %s", exc)
        return []


def main() -> None:
    args = _parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.services.market_intelligence_service import build_closing_lines

    conn = get_local_db()
    init_schema(conn)

    if args.fixture:
        provider_fixture_ids = [args.fixture]
        print(f"Fixture único: {args.fixture}")
    else:
        provider_fixture_ids = _get_fixtures_with_history(conn, args.days)
        print(f"Fixtures con historial de cuotas: {len(provider_fixture_ids)} (últimos {args.days} día(s))")

    if not provider_fixture_ids:
        print("No hay datos de odds history. Ejecuta sync_market_odds.py primero.")
        return

    print(f"Dry-run: {args.dry_run}")

    result = build_closing_lines(conn, provider_fixture_ids, dry_run=args.dry_run)

    print(
        f"\nResultado:"
        f"\n  Líneas computadas   : {result['processed']}"
        f"\n  Actualizadas en DB  : {result['updated']}"
        f"\n  Fixtures sin datos  : {result['skipped']}"
        + (" [DRY-RUN]" if args.dry_run else "")
    )


if __name__ == "__main__":
    main()
