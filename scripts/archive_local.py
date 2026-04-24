#!/usr/bin/env python
"""Export warm-tier rows to local Parquet files before retention cleanup.

Only runs when LOCAL_ARCHIVE_ENABLED=true in .env.
If LOCAL_ARCHIVE_BEFORE_DELETE=true, also deletes archived rows from Supabase.

Requires: pip install duckdb  (pandas is already in requirements.txt)

Output structure:
    data/archive/<table>/<YYYY-MM-DD>.parquet

Usage:
    python scripts/archive_local.py           # archive + optionally delete
    python scripts/archive_local.py --dry-run # preview only
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.core.config import settings
    from app.core.logger import setup_logger
    from app.data.repositories.supabase_client import get_supabase
except ImportError as e:
    print(f"Error de importación: {e}")
    sys.exit(1)

logger = logging.getLogger(__name__)

# Tables to archive: (table_name, timestamp_column, settings_attr_for_days)
ARCHIVE_TARGETS = [
    ("published_picks",         "published_at",  "retention_published_picks_days"),
    ("pick_results",            "created_at",    "retention_settlement_days"),
    ("api_sync_runs",           "started_at",    "retention_sync_runs_days"),
    ("api_usage_snapshots",     "captured_at",   "retention_usage_days"),
]


def _require_duckdb():
    try:
        import duckdb
        return duckdb
    except ImportError:
        print(
            "ERROR: duckdb no está instalado.\n"
            "Instala con: pip install duckdb\n"
            "O desactiva el archivado: LOCAL_ARCHIVE_ENABLED=false"
        )
        sys.exit(1)


def _require_pandas():
    try:
        import pandas as pd
        return pd
    except ImportError:
        print("ERROR: pandas no está instalado. Instala con: pip install pandas")
        sys.exit(1)


def archive_table(
    client,
    duck,
    pd,
    table: str,
    ts_col: str,
    days: int,
    archive_dir: Path,
    date_tag: str,
    dry_run: bool,
    delete_after: bool,
) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    result = client.table(table).select("*").lt(ts_col, cutoff).execute()
    rows = result.data or []

    if not rows:
        logger.info("archive %s: sin filas antiguas (>%d días)", table, days)
        return 0

    if dry_run:
        logger.info("[dry-run] archive %s: %d filas candidatas (>%d días)", table, len(rows), days)
        return len(rows)

    out_dir = archive_dir / table
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{date_tag}.parquet"

    df = pd.DataFrame(rows)
    duck.sql("SELECT * FROM df").write_parquet(str(out_path))
    logger.info("archive %s: %d filas → %s", table, len(rows), out_path)

    if delete_after:
        ids = [r["id"] for r in rows]
        for i in range(0, len(ids), 500):
            client.table(table).delete().in_("id", ids[i : i + 500]).execute()
        logger.info("archive %s: %d filas eliminadas de Supabase", table, len(rows))

    return len(rows)


def main(dry_run: bool) -> None:
    if not settings.local_archive_enabled:
        print("Archivado local desactivado (LOCAL_ARCHIVE_ENABLED=false). Sin acción.")
        return

    if not dry_run:
        duck = _require_duckdb()
        pd = _require_pandas()
    else:
        duck = None
        pd = None

    client = get_supabase()
    archive_dir = Path(settings.local_archive_dir)
    delete_after = settings.local_archive_before_delete
    date_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    tag = "[DRY RUN]" if dry_run else f"→ {archive_dir}"
    print(f"\n=== Archive local {tag} ===")

    total = 0
    for table, ts_col, ttl_attr in ARCHIVE_TARGETS:
        days = getattr(settings, ttl_attr)
        n = archive_table(
            client=client,
            duck=duck,
            pd=pd,
            table=table,
            ts_col=ts_col,
            days=days,
            archive_dir=archive_dir,
            date_tag=date_tag,
            dry_run=dry_run,
            delete_after=delete_after,
        )
        total += n
        verb = "archivaría" if dry_run else "archivadas"
        marker = "  !" if n > 0 else "   "
        print(f"{marker} {table:<34} {n:>6} filas {verb}")

    print(f"   {'TOTAL':<34} {total:>6}")

    if not dry_run and total > 0:
        if delete_after:
            print("\n  Filas eliminadas de Supabase después de archivar.")
        else:
            print(
                "\n  Filas conservadas en Supabase (LOCAL_ARCHIVE_BEFORE_DELETE=false).\n"
                "  Para eliminarlas, activa LOCAL_ARCHIVE_BEFORE_DELETE=true y vuelve a correr."
            )
    elif dry_run:
        print("\n  Dry-run — no se escribió nada.")
    print()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Archive warm-tier Supabase rows to local Parquet files.",
        epilog=(
            "Examples:\n"
            "  python scripts/archive_local.py --dry-run\n"
            "  python scripts/archive_local.py\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Count rows to archive without writing or deleting anything.",
    )
    return p.parse_args()


if __name__ == "__main__":
    setup_logger()
    args = parse_args()
    main(dry_run=args.dry_run)
