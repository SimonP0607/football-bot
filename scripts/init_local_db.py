#!/usr/bin/env python
"""Initialize the local DuckDB history database.

Creates the database file and applies the schema (idempotent).
Safe to run multiple times — uses IF NOT EXISTS throughout.

Usage:
    python scripts/init_local_db.py
    python scripts/init_local_db.py --path ./data/custom.duckdb
"""

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.core.config import settings
    from app.core.logger import setup_logger
    from app.data.local.duckdb_client import get_local_db, init_schema, table_names
except ImportError as e:
    print(f"Error de importación: {e}")
    sys.exit(1)

logger = logging.getLogger(__name__)

EXPECTED_TABLES = [
    # 001_history_schema.sql
    "fixtures_history",
    "standings_history",
    "team_stats_history",
    "odds_history",
    "published_picks_history",
    "pick_results_history",
    "backtest_runs",
    "backtest_metrics",
    "history_metadata",
    # 002_backtest_schema.sql
    "competition_context",
    "team_elo_history",
    "training_samples",
]


def main(db_path: str | None) -> None:
    path = db_path or settings.local_db_path
    resolved = Path(path).resolve()

    print(f"\n=== init_local_db ===")
    print(f"Base de datos: {resolved}")

    conn = get_local_db(path)
    n = init_schema(conn)
    print(f"Schema aplicado: {n} statements")

    tables = table_names(conn)
    print(f"\nTablas creadas ({len(tables)}):")
    for t in tables:
        mark = "  OK" if t in EXPECTED_TABLES else "  ?"
        print(f"{mark} {t}")

    missing = [t for t in EXPECTED_TABLES if t not in tables]
    if missing:
        print(f"\n  FAIL — Tablas faltantes: {', '.join(missing)}")
        sys.exit(1)

    print(f"\nOK — Base local lista en: {resolved}\n")
    print("Próximos pasos:")
    print("  python scripts/test_local_db.py    # verificar inserción y lectura")
    print("  python scripts/archive_local.py    # archivar datos warm de Supabase (opcional)")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Initialize local DuckDB history database.")
    p.add_argument(
        "--path",
        default=None,
        help="Override LOCAL_DB_PATH from .env.",
    )
    return p.parse_args()


if __name__ == "__main__":
    setup_logger()
    args = parse_args()
    main(args.path)
