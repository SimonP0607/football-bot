"""Database health and schema-readiness checks.

Used at bot startup (_post_init) and by the /estado command to report the
real state of the database without crashing the bot.

Design goals:
  - Never raises — always returns a SchemaStatus even on total failure.
  - Uses limit(0) probes so no actual data is read, and the query is fast.
  - Imports supabase_client lazily (inside the function) to avoid circular
    import issues during module load.
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Every table that must exist before the bot can serve data.
CRITICAL_TABLES = [
    "bot_users",
    "leagues",
    "teams",
    "fixtures",
    "odds_snapshots",
    "predictions",
    "prediction_results",
]


@dataclass
class SchemaStatus:
    """Result of a schema-readiness probe."""

    connection_ok: bool = False
    tables_ok: bool = False
    missing_tables: list[str] = field(default_factory=list)
    # Human-readable error when connection itself failed
    error: str | None = None

    @property
    def db_ready(self) -> bool:
        """True only when connected AND every critical table exists."""
        return self.connection_ok and self.tables_ok

    @property
    def label(self) -> str:
        """Short status tag for logs and /estado."""
        if not self.connection_ok:
            return "CONN_FAIL"
        if not self.tables_ok:
            return "DB_NOT_INITIALIZED"
        return "OK"


def check_schema() -> SchemaStatus:
    """Probe each critical table with a zero-row SELECT.

    Returns a :class:`SchemaStatus` describing the current state.
    Never raises — always returns a status object even on total failure.
    """
    status = SchemaStatus()

    # Step 1 — can we even create a client?
    try:
        from app.data.repositories.supabase_client import get_supabase  # lazy import

        client = get_supabase()
        status.connection_ok = True
    except Exception as exc:
        status.error = str(exc)
        logger.error("DB health: no se pudo crear el cliente Supabase — %s", exc)
        return status

    # Step 2 — probe each table with SELECT id LIMIT 0
    missing: list[str] = []
    for table in CRITICAL_TABLES:
        try:
            client.table(table).select("id").limit(0).execute()
        except Exception as exc:
            missing.append(table)
            logger.debug("DB health: tabla '%s' no encontrada — %s", table, exc)

    status.missing_tables = missing
    status.tables_ok = not missing

    if not status.tables_ok:
        logger.error(
            "DB_NOT_INITIALIZED — %d tabla(s) faltante(s): %s",
            len(missing),
            ", ".join(missing),
        )
    else:
        logger.info("DB health: schema OK — %d tablas verificadas", len(CRITICAL_TABLES))

    return status
