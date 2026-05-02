#!/usr/bin/env python
"""Audit Phase 4 availability data in DuckDB.

Shows:
  - Row counts per availability table
  - Coverage by league (fixtures with/without data)
  - Impact label distribution
  - Fixtures with lineups
  - Estimated API call cost of a sync run

Usage:
    python scripts/audit_availability.py
    python scripts/audit_availability.py --days 3
    python scripts/audit_availability.py --league 39
    python scripts/audit_availability.py --json
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: F401


def _open_conn():
    from app.data.local.duckdb_client import get_local_db, init_schema
    conn = get_local_db()
    init_schema(conn)
    return conn


def _has_availability_tables(conn) -> bool:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='main' AND table_name='team_availability_summary'"
    ).fetchall()
    return bool(rows)


def main(args: argparse.Namespace) -> None:
    conn = _open_conn()

    if not _has_availability_tables(conn):
        print("\n  Tablas de disponibilidad no encontradas.")
        print("  Ejecuta: python scripts/init_local_db.py")
        return

    from app.data.local.availability_repo import availability_counts

    counts = availability_counts(conn)
    report: dict = {"counts": counts, "tables_ok": True}

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return

    print()
    print("=" * 60)
    print("  AUDIT AVAILABILITY — DuckDB local")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
    print("=" * 60)

    print()
    print("  Conteos de tablas:")
    for table, count in counts.items():
        print(f"    {table:<42} {count:>6} filas")

    if counts.get("team_availability_summary", 0) == 0:
        print()
        print("  Sin datos de disponibilidad. Ejecuta:")
        print("  python scripts/sync_availability_today.py --execute --days 1")
        print()
        return

    # ── Coverage by impact label ──────────────────────────────────────────────
    print()
    print("  Distribución de impacto (team_availability_summary):")
    try:
        rows = conn.execute(
            """
            SELECT impact_label, coverage_status, COUNT(*) as n
            FROM team_availability_summary
            GROUP BY impact_label, coverage_status
            ORDER BY impact_label, coverage_status
            """
        ).fetchall()
        if rows:
            print(f"  {'Impacto':<12} {'Cobertura':<12} {'Equipos':>8}")
            print("  " + "-" * 34)
            for row in rows:
                print(f"  {(row[0] or '?'):<12} {(row[1] or '?'):<12} {row[2]:>8}")
    except Exception as exc:
        print(f"    Error: {exc}")

    # ── Fixtures with availability data ───────────────────────────────────────
    print()
    print("  Fixtures con datos de disponibilidad:")
    try:
        rows = conn.execute(
            """
            SELECT COUNT(DISTINCT provider_fixture_id) as fixtures_with_data,
                   COUNT(*) as team_rows,
                   SUM(CASE WHEN coverage_status='data' THEN 1 ELSE 0 END) as rows_with_data,
                   SUM(CASE WHEN coverage_status='no_data' THEN 1 ELSE 0 END) as rows_no_data,
                   SUM(CASE WHEN coverage_status='api_error' THEN 1 ELSE 0 END) as rows_api_error
            FROM team_availability_summary
            """
        ).fetchone()
        if rows:
            print(f"    Fixtures únicos:         {rows[0]}")
            print(f"    Filas equipo total:      {rows[1]}")
            print(f"    Con datos injuries:      {rows[2]}")
            print(f"    Sin datos (no_data):     {rows[3]}")
            print(f"    Errores API:             {rows[4]}")
    except Exception as exc:
        print(f"    Error: {exc}")

    # ── Fixtures with lineups ─────────────────────────────────────────────────
    print()
    try:
        lu_count = conn.execute(
            "SELECT COUNT(DISTINCT provider_fixture_id) FROM fixture_lineups_history"
        ).fetchone()
        lu_rows = conn.execute("SELECT COUNT(*) FROM fixture_lineups_history").fetchone()
        print(f"  Lineups: {lu_rows[0] if lu_rows else 0} filas "
              f"({lu_count[0] if lu_count else 0} fixtures únicos)")
    except Exception as exc:
        print(f"    Error lineups: {exc}")

    # ── League filter ─────────────────────────────────────────────────────────
    if args.league:
        print()
        print(f"  Detalle liga {args.league}:")
        try:
            rows = conn.execute(
                """
                SELECT s.provider_fixture_id, s.team_id,
                       s.impact_label, s.coverage_status, s.missing_count
                FROM team_availability_summary s
                WHERE s.league_id = ?
                ORDER BY s.provider_fixture_id, s.team_id
                LIMIT 50
                """,
                [args.league],
            ).fetchall()
            if rows:
                print(f"  {'Fixture':<12} {'Team':>8} {'Impacto':<10} {'Cobertura':<12} {'Bajas':>5}")
                print("  " + "-" * 50)
                for r in rows:
                    print(
                        f"  {r[0]:<12} {r[1]:>8} {(r[2] or '?'):<10} {(r[3] or '?'):<12} {r[4]:>5}"
                    )
            else:
                print(f"    Sin datos para liga {args.league}")
                print("    Nota: league_id en availability = provider_league_id de API-Football")
        except Exception as exc:
            print(f"    Error: {exc}")

    # ── Estimated cost of a full sync ─────────────────────────────────────────
    print()
    try:
        from app.data.supabase_reader import get_fixtures_for_range
        now = datetime.now(timezone.utc)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=args.days)
        fixtures = get_fixtures_for_range(start.isoformat(), end.isoformat(), limit=200)
        n = len(fixtures)
        print(f"  Fixtures hoy en Supabase: {n}")
        print(f"  Coste estimado (injuries+lineups): {n * 2} llamadas API")
        print(f"  Solo injuries:                     {n} llamadas API")
    except Exception as exc:
        print(f"    No se pudo consultar Supabase: {exc}")

    print()
    print("  Comandos:")
    print("    python scripts/sync_availability_today.py --dry-run --days 1 --limit 30")
    print("    python scripts/sync_availability_today.py --execute --days 1 --limit 30")
    print("    python scripts/report_availability.py --fixture <id>")
    print()
    print("=" * 60)
    print()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Audita datos de disponibilidad en DuckDB")
    p.add_argument("--days", type=int, default=1, metavar="N",
                   help="Días a analizar (defecto: 1)")
    p.add_argument("--league", type=int, default=None, metavar="ID",
                   help="Mostrar detalle de esta liga")
    p.add_argument("--json", action="store_true", help="Salida JSON")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    main(args)
