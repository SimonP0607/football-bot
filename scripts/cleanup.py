#!/usr/bin/env python
"""Retention cleanup — applies TTL policy to Supabase hot/warm/cache tables.

Calls cleanup_retention() or cleanup_retention_preview() via Supabase RPC.
TTL values are read from Settings (app/core/config.py / .env).

Usage:
    python scripts/cleanup.py           # real cleanup
    python scripts/cleanup.py --dry-run # preview — nothing is deleted
"""

import argparse
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.core.config import settings
    from app.core.logger import setup_logger
    from app.data.repositories.supabase_client import get_supabase
except ImportError as e:
    print(f"Error de importación: {e}")
    sys.exit(1)

logger = logging.getLogger(__name__)


def _rpc_params() -> dict:
    return {
        "p_fixtures_days":        settings.retention_fixtures_days,
        "p_odds_days":            settings.retention_odds_days,
        "p_context_days":         settings.retention_context_days,
        "p_candidates_days":      settings.retention_candidates_days,
        "p_published_picks_days": settings.retention_published_picks_days,
        "p_settlement_days":      settings.retention_settlement_days,
        "p_sync_runs_days":       settings.retention_sync_runs_days,
        "p_usage_days":           settings.retention_usage_days,
        "p_h2h_days":             settings.retention_h2h_days,
        "p_team_metrics_days":    settings.retention_team_metrics_days,
        "p_market_cache_days":    settings.retention_market_cache_days,
        "p_cron_history_days":    settings.retention_cron_history_days,
    }


def run_preview(client) -> list[dict]:
    result = client.rpc("cleanup_retention_preview", _rpc_params()).execute()
    return result.data or []


def run_cleanup(client) -> list[dict]:
    result = client.rpc("cleanup_retention", _rpc_params()).execute()
    return result.data or []


def _print_summary(rows: list[dict], dry_run: bool, duration: float) -> None:
    col_key = "rows_to_delete" if dry_run else "rows_deleted"
    tag = "[DRY RUN]" if dry_run else "[REAL]"
    total = sum(r.get(col_key, 0) or 0 for r in rows)

    print(f"\n=== Cleanup {tag} — {duration:.1f}s ===")
    for row in rows:
        tbl = row.get("tbl", "?")
        n = row.get(col_key, 0) or 0
        verb = "eliminaría" if dry_run else "eliminadas"
        marker = "  !" if n > 0 else "   "
        print(f"{marker} {tbl:<34} {n:>6} filas {verb}")
    print(f"   {'TOTAL':<34} {total:>6}")
    if dry_run:
        print("\n  Modo dry-run — sin cambios. Corre sin --dry-run para aplicar.")
    archive_status = "activado" if settings.local_archive_enabled else "desactivado"
    print(f"  Archivado local: {archive_status}")
    print()


def main(dry_run: bool) -> None:
    client = get_supabase()
    t0 = time.monotonic()

    if dry_run:
        logger.info("Cleanup dry-run iniciado")
        rows = run_preview(client)
    else:
        logger.info("Cleanup real iniciado")
        rows = run_cleanup(client)

    duration = time.monotonic() - t0
    _print_summary(rows, dry_run=dry_run, duration=duration)

    if not dry_run:
        logger.info("Cleanup completado en %.1fs", duration)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Retention cleanup for football-bot Supabase DB.",
        epilog=(
            "TTL values come from .env / Settings. Apply migration 013 first.\n\n"
            "Examples:\n"
            "  python scripts/cleanup.py --dry-run\n"
            "  python scripts/cleanup.py\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Preview rows to delete without deleting anything.",
    )
    return p.parse_args()


if __name__ == "__main__":
    setup_logger()
    args = parse_args()
    main(dry_run=args.dry_run)
