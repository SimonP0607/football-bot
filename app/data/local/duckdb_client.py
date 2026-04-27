"""Local DuckDB connection manager for the football-bot history database.

Provides a singleton connection per process. The database file is created
automatically on first connection. Schema is applied separately via init_schema().

Usage:
    from app.data.local.duckdb_client import get_local_db, init_schema

    conn = get_local_db()
    init_schema(conn)                    # idempotent — safe to call every time
    conn.execute("SELECT 1").fetchone()
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import duckdb

logger = logging.getLogger(__name__)

_SCHEMA_DIR = Path(__file__).parent.parent.parent.parent / "sql" / "local"

_connection: Optional[duckdb.DuckDBPyConnection] = None


def get_local_db(db_path: str | None = None) -> duckdb.DuckDBPyConnection:
    """Return a persistent connection to the local history DuckDB database.

    Creates the database file and its parent directories if they don't exist.
    Subsequent calls return the same connection (singleton per process).

    Args:
        db_path: Override the path from Settings. Only honoured on the first call.
    """
    global _connection
    if _connection is not None:
        return _connection

    from app.core.config import settings
    path = db_path or settings.local_db_path

    db_file = Path(path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    _connection = duckdb.connect(str(db_file))
    logger.info("DuckDB conectado: %s", db_file.resolve())
    return _connection


def _apply_sql_file(conn: duckdb.DuckDBPyConnection, path: Path) -> int:
    """Execute all statements in a single .sql file. Returns statement count."""
    sql = path.read_text(encoding="utf-8")
    executed = 0
    for chunk in sql.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        has_sql = any(
            line.strip() and not line.strip().startswith("--")
            for line in chunk.splitlines()
        )
        if not has_sql:
            continue
        conn.execute(chunk)
        executed += 1
    return executed


def init_schema(conn: duckdb.DuckDBPyConnection) -> int:
    """Apply all schema migrations in sql/local/ (idempotent).

    Applies every *.sql file in sql/local/ sorted alphabetically, so
    001_history_schema.sql runs before 002_backtest_schema.sql, etc.
    New migration files are picked up automatically without code changes.

    Returns:
        Total number of SQL statements executed across all files.
    """
    sql_files = sorted(_SCHEMA_DIR.glob("*.sql"))
    total = 0
    for f in sql_files:
        n = _apply_sql_file(conn, f)
        logger.debug("  %s: %d statements", f.name, n)
        total += n
    logger.info("Schema aplicado: %d statements (%d archivos)", total, len(sql_files))
    return total


def close_local_db() -> None:
    """Close the singleton connection (mainly for tests / cleanup)."""
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None
        logger.debug("DuckDB connection cerrada")


def table_names(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """Return the list of table names in the database."""
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY 1"
    ).fetchall()
    return [r[0] for r in rows]
