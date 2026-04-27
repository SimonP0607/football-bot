#!/usr/bin/env python
"""Pre-flight check for Supabase connectivity and schema.

Run this AFTER applying migrations and BEFORE starting the bot.

Usage:
    python scripts/check_supabase.py

Exit codes:
    0 — everything OK
    1 — connection or schema error (see output for details)
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env before config is imported
from dotenv import load_dotenv
load_dotenv()

# Required tables — mirrors CRITICAL_TABLES in app/data/db_health.py
REQUIRED_TABLES = [
    "bot_users",
    "competitions",
    "competition_seasons",
    "tracked_competitions",
    "teams",
    "fixtures",
    "fixture_contexts",
    "odds_snapshots",
    "pick_candidates",
    "published_picks",
    "pick_results",
    "ref_bookmakers",
    "ref_bet_types",
    "api_sync_runs",
    "api_usage_snapshots",
]


def _check_env() -> bool:
    """Verify that the essential env vars are present."""
    ok = True
    for var in ("SUPABASE_URL", "SUPABASE_KEY"):
        val = os.getenv(var, "")
        if not val or "<COMPLETAR>" in val:
            print(f"  ✗ {var} no está configurado en .env")
            ok = False
        else:
            masked = val[:20] + "..." if len(val) > 20 else val
            print(f"  ✓ {var} = {masked}")
    return ok


def _check_tables(client) -> bool:
    """Try a minimal SELECT on each required table."""
    all_ok = True
    for table in REQUIRED_TABLES:
        try:
            result = client.table(table).select("id").limit(1).execute()
            row_count = len(result.data) if result.data else 0
            print(f"  ✓ {table:<22} (filas encontradas en muestra: {row_count})")
        except Exception as exc:
            print(f"  ✗ {table:<22} ERROR: {exc}")
            all_ok = False
    return all_ok


def main() -> bool:
    print("\n=== check_supabase.py ===\n")

    print("[ 1 ] Variables de entorno")
    if not _check_env():
        print("\nCorrige .env antes de continuar.")
        return False

    print("\n[ 2 ] Conexión y tablas")
    try:
        from app.data.repositories.supabase_client import get_supabase
        client = get_supabase()
    except Exception as exc:
        print(f"  ✗ No se pudo crear el cliente de Supabase: {exc}")
        return False

    tables_ok = _check_tables(client)

    print("\n[ 3 ] Conteos actuales")
    try:
        for table in ("fixtures", "odds_snapshots", "pick_candidates", "published_picks"):
            result = client.table(table).select("id", count="exact").execute()
            print(f"  {table:<22} {result.count or 0} filas")
    except Exception as exc:
        print(f"  ✗ No se pudo obtener conteos: {exc}")

    print()
    if tables_ok:
        print("✅  Supabase OK — el bot puede conectar correctamente.")
    else:
        print(
            "❌  Algunas tablas no existen. Aplica las migraciones SQL:\n"
            "    Supabase → SQL Editor → pega y ejecuta:\n"
            "      sql/migrations/010_production_schema.sql  (instala limpia)\n"
            "      sql/migrations/011_sync_tier.sql\n"
            "      sql/migrations/012_settlement.sql\n"
            "      sql/migrations/013_retention_cleanup.sql"
        )
    return tables_ok


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
