#!/usr/bin/env python
"""Retention cleanup — deletes stale hot-layer data to avoid DB bloat.

Retention policy:
  odds_snapshots   — deleted 7 days after fixture kickoff (for finished matches)
  fixture_contexts — deleted 14 days after fixture kickoff (for finished matches)
  pick_candidates  — kept forever (historical audit trail)
  published_picks  — kept forever
  fixtures         — kept forever (catalog)
  api_sync_runs    — kept for 90 days
  api_usage_snapshots — kept for 30 days

Finished match statuses (API-Football):
  FT, AET, PEN, AWD, WO, CANC, ABD, POSTP

Usage:
    python scripts/cleanup.py
    python scripts/cleanup.py --odds-days 7 --context-days 14 --dry-run
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.core.logger import setup_logger
except ImportError as _e:
    print(f"Error de importación: {_e}")
    sys.exit(1)

from app.data.repositories.supabase_client import get_supabase

logger = logging.getLogger(__name__)

FINISHED_STATUSES = ("FT", "AET", "PEN", "AWD", "WO", "CANC", "ABD", "POSTP")


def _get_old_finished_fixture_ids(client, cutoff_days: int) -> list[int]:
    """Return IDs of finished fixtures whose kickoff_at is older than cutoff_days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=cutoff_days)).isoformat()
    result = (
        client.table("fixtures")
        .select("id")
        .lt("kickoff_at", cutoff)
        .in_("status_short", list(FINISHED_STATUSES))
        .execute()
    )
    return [row["id"] for row in (result.data or [])]


def _delete_in_batches(client, table: str, column: str, ids: list[int], dry_run: bool) -> int:
    """Delete rows from table WHERE column IN ids. Works in batches of 500."""
    if not ids:
        return 0
    total = 0
    batch_size = 500
    for i in range(0, len(ids), batch_size):
        batch = ids[i: i + batch_size]
        if dry_run:
            logger.info("[dry-run] Eliminaría %d filas de %s", len(batch), table)
            total += len(batch)
        else:
            client.table(table).delete().in_(column, batch).execute()
            total += len(batch)
    return total


def cleanup_odds(client, days: int, dry_run: bool) -> int:
    """Delete odds_snapshots for finished fixtures older than `days` days."""
    fixture_ids = _get_old_finished_fixture_ids(client, days)
    if not fixture_ids:
        logger.info("cleanup_odds: sin fixtures candidatos (>%d días + terminados)", days)
        return 0
    n = _delete_in_batches(client, "odds_snapshots", "fixture_id", fixture_ids, dry_run)
    logger.info(
        "cleanup_odds: %s %d filas de odds_snapshots (%d fixtures, >%d días)",
        "eliminaría" if dry_run else "eliminadas", n, len(fixture_ids), days,
    )
    return n


def cleanup_contexts(client, days: int, dry_run: bool) -> int:
    """Delete fixture_contexts for finished fixtures older than `days` days."""
    fixture_ids = _get_old_finished_fixture_ids(client, days)
    if not fixture_ids:
        logger.info("cleanup_contexts: sin fixtures candidatos (>%d días + terminados)", days)
        return 0
    n = _delete_in_batches(client, "fixture_contexts", "fixture_id", fixture_ids, dry_run)
    logger.info(
        "cleanup_contexts: %s %d filas de fixture_contexts (%d fixtures, >%d días)",
        "eliminaría" if dry_run else "eliminadas", n, len(fixture_ids), days,
    )
    return n


def cleanup_sync_runs(client, days: int, dry_run: bool) -> int:
    """Delete api_sync_runs older than `days` days (finished runs only)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if dry_run:
        result = (
            client.table("api_sync_runs")
            .select("id", count="exact")
            .lt("started_at", cutoff)
            .neq("status", "running")
            .execute()
        )
        n = result.count or 0
        logger.info("[dry-run] Eliminaría %d filas de api_sync_runs (>%d días)", n, days)
        return n
    else:
        client.table("api_sync_runs").delete().lt("started_at", cutoff).neq("status", "running").execute()
        logger.info("cleanup_sync_runs: eliminadas filas >%d días", days)
        return 0  # PostgREST delete doesn't return count reliably


def cleanup_usage_snapshots(client, days: int, dry_run: bool) -> int:
    """Delete api_usage_snapshots older than `days` days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if dry_run:
        result = (
            client.table("api_usage_snapshots")
            .select("id", count="exact")
            .lt("captured_at", cutoff)
            .execute()
        )
        n = result.count or 0
        logger.info("[dry-run] Eliminaría %d filas de api_usage_snapshots (>%d días)", n, days)
        return n
    else:
        client.table("api_usage_snapshots").delete().lt("captured_at", cutoff).execute()
        logger.info("cleanup_usage_snapshots: eliminadas filas >%d días", days)
        return 0


def main(
    odds_days: int,
    context_days: int,
    sync_runs_days: int,
    usage_days: int,
    dry_run: bool,
) -> None:
    if dry_run:
        logger.info("=== Cleanup (DRY RUN — no se elimina nada) ===")
    else:
        logger.info("=== Cleanup — iniciando limpieza de retención ===")

    client = get_supabase()

    n_odds = cleanup_odds(client, odds_days, dry_run)
    n_ctx = cleanup_contexts(client, context_days, dry_run)
    n_runs = cleanup_sync_runs(client, sync_runs_days, dry_run)
    n_usage = cleanup_usage_snapshots(client, usage_days, dry_run)

    logger.info(
        "=== Cleanup completado | odds=%d ctx=%d sync_runs=%d usage=%d ===",
        n_odds, n_ctx, n_runs, n_usage,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Retention cleanup for football-bot Supabase DB.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Deletes stale hot-layer data. Permanent data (fixtures, pick_candidates,\n"
            "published_picks) is never deleted.\n\n"
            "Examples:\n"
            "  python scripts/cleanup.py --dry-run\n"
            "  python scripts/cleanup.py --odds-days 7 --context-days 14\n"
        ),
    )
    parser.add_argument(
        "--odds-days", default=7, type=int,
        help="Delete odds_snapshots for finished fixtures older than N days (default: 7).",
    )
    parser.add_argument(
        "--context-days", default=14, type=int,
        help="Delete fixture_contexts for finished fixtures older than N days (default: 14).",
    )
    parser.add_argument(
        "--sync-runs-days", default=90, type=int,
        help="Delete api_sync_runs older than N days (default: 90).",
    )
    parser.add_argument(
        "--usage-days", default=30, type=int,
        help="Delete api_usage_snapshots older than N days (default: 30).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be deleted without actually deleting anything.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    setup_logger()
    args = parse_args()
    main(
        odds_days=args.odds_days,
        context_days=args.context_days,
        sync_runs_days=args.sync_runs_days,
        usage_days=args.usage_days,
        dry_run=args.dry_run,
    )
