#!/usr/bin/env python
"""Database bootstrap guide for football-bot.

Checks Supabase connectivity and schema readiness, then provides
exact instructions — and prints the full SQL to copy-paste — so you
can initialize the database without having to hunt for the files.

Usage:
    python scripts/bootstrap_database.py

What it does:
    1. Validates environment variables
    2. Verifies the migration SQL files exist locally
    3. Connects to Supabase and checks the schema
    4. If tables are missing: prints the SQL content to copy into the SQL Editor
    5. If tables are present: confirms readiness and shows next steps

Note on direct SQL execution:
    Supabase PostgREST does NOT allow executing arbitrary DDL (CREATE TABLE,
    ALTER TABLE) through the REST API. Migrations must be applied via:
      - Supabase Dashboard → SQL Editor  (recommended)
      - Direct Postgres connection (DATABASE_URL, requires pg driver)
    This script guides you through the SQL Editor method.

Exit codes:
    0 — DB is ready (all tables exist)
    1 — DB not ready or env not configured
"""

import sys
import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

MIGRATION_FILES = [
    ("sql/migrations/001_init.sql", "Crea todas las tablas base"),
    ("sql/migrations/002_constraints_and_indexes.sql", "Añade constraints únicos e índices"),
]

_LINE = "─" * 60
_DLINE = "═" * 60


def _section(title: str) -> None:
    print(f"\n[ {title} ]")


def _check_env() -> bool:
    ok = True
    for var in ("SUPABASE_URL", "SUPABASE_KEY"):
        val = os.getenv(var, "")
        if not val or "<COMPLETAR>" in val:
            print(f"  ✗ {var}  →  no configurado")
            ok = False
        else:
            masked = val[:20] + "..." if len(val) > 20 else val
            print(f"  ✓ {var}  →  {masked}")
    return ok


def _check_files() -> bool:
    all_ok = True
    for rel_path, desc in MIGRATION_FILES:
        full = _PROJECT_ROOT / rel_path
        if full.is_file():
            size = full.stat().st_size
            print(f"  ✓ {rel_path}  ({size} bytes) — {desc}")
        else:
            print(f"  ✗ {rel_path}  →  NO ENCONTRADO")
            all_ok = False
    return all_ok


def _connect():
    """Return supabase client or None."""
    try:
        from app.data.repositories.supabase_client import get_supabase
        client = get_supabase()
        print("  ✓ Conexión establecida")
        return client
    except Exception as exc:
        print(f"  ✗ No se pudo conectar: {exc}")
        return None


def _check_schema():
    from app.data.db_health import check_schema, CRITICAL_TABLES
    schema = check_schema()
    if schema.tables_ok:
        print(f"  ✓ Todas las tablas existen ({len(CRITICAL_TABLES)} tablas)")
    else:
        present = [t for t in CRITICAL_TABLES if t not in schema.missing_tables]
        print(f"  ✓ Tablas presentes ({len(present)}): {', '.join(present) or 'ninguna'}")
        print(f"  ✗ Tablas faltantes ({len(schema.missing_tables)}): {', '.join(schema.missing_tables)}")
    return schema


def _print_migration_instructions() -> None:
    print(f"\n{_DLINE}")
    print("  INSTRUCCIONES PARA APLICAR LAS MIGRACIONES")
    print(_DLINE)
    print()
    print("  Las migraciones deben ejecutarse en el SQL Editor de Supabase.")
    print("  La API REST no permite ejecutar DDL (CREATE TABLE, ALTER TABLE).")
    print()
    print("  PASOS:")
    print("    1. Ve a  https://supabase.com  →  tu proyecto  →  SQL Editor")
    print("    2. Haz clic en  'New query'")
    print("    3. Copia y pega el contenido del ARCHIVO 1 (ver abajo)")
    print("    4. Haz clic en  'Run'  →  debe decir 'Success'")
    print("    5. Crea otra query y repite con el ARCHIVO 2")
    print("    6. Vuelve a ejecutar este script para confirmar")
    print()
    print("  CONSEJO: Si ya tienes tablas de una instalación anterior,")
    print("  los scripts usan IF NOT EXISTS y son seguros de re-ejecutar.")

    for i, (rel_path, desc) in enumerate(MIGRATION_FILES, 1):
        full = _PROJECT_ROOT / rel_path
        print()
        print(_LINE)
        print(f"  ARCHIVO {i} DE {len(MIGRATION_FILES)}:  {rel_path}")
        print(f"  Descripción: {desc}")
        print(_LINE)
        if full.is_file():
            sql = full.read_text(encoding="utf-8").strip()
            print()
            print(sql)
            print()
        else:
            print(f"\n  [Archivo no encontrado localmente]\n")


def _print_next_steps() -> None:
    print(f"\n{_DLINE}")
    print("  PRÓXIMOS PASOS (en orden)")
    print(_DLINE)
    print()
    print("  1. Verificación de Supabase:")
    print("       python scripts/check_supabase.py")
    print()
    print("  2. Verificación de API-Football:")
    print("       python scripts/check_api_football.py --league 39 --season 2025")
    print()
    print("  3. Sincronizar fixtures y odds de hoy:")
    print("       python scripts/sync_today.py")
    print()
    print("  4. Arrancar el bot:")
    print("       python run.py")
    print()
    print("  5. En Telegram, envía /estado para confirmar que todo está OK")
    print()


def main() -> bool:
    print("\n=== bootstrap_database.py ===")
    print("Guía de inicialización de la base de datos\n")

    _section("1 — Variables de entorno")
    if not _check_env():
        print("\n  ⚠  Completa .env antes de continuar.")
        print("     Copia .env.example como .env y rellena los valores.")
        return False

    _section("2 — Archivos de migración locales")
    files_ok = _check_files()
    if not files_ok:
        print()
        print("  ⚠  Faltan archivos de migración.")
        print("     Asegúrate de estar en el directorio raíz del proyecto.")
        return False

    _section("3 — Conexión a Supabase")
    client = _connect()
    if client is None:
        print()
        print("  ⚠  Verifica SUPABASE_URL y SUPABASE_KEY en .env.")
        return False

    _section("4 — Estado del schema")
    schema = _check_schema()

    if schema.tables_ok:
        print(f"\n{'✅ ' * 1} Base de datos lista. No hay nada que inicializar.")
        _print_next_steps()
        return True

    # Tables are missing — provide full instructions + SQL content
    _print_migration_instructions()

    print(f"\n{_DLINE}")
    print("  Después de aplicar las migraciones:")
    print("    python scripts/bootstrap_database.py   ← vuelve a ejecutar para verificar")
    print(_DLINE)
    print()

    return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
