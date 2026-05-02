#!/usr/bin/env python
"""Sync player availability (injuries + lineups) for today's fixtures.

Reads today's fixtures from Supabase, fetches injuries and lineups from
API-Football, and writes to the local DuckDB availability tables.

Budget: injuries and lineups are 'low' priority — skipped when quota is tight.
Each fixture costs 1-2 API calls (1 for injuries + 1 for lineups if requested).

Usage:
    python scripts/sync_availability_today.py --dry-run --days 1 --limit 30
    python scripts/sync_availability_today.py --execute --days 1 --limit 30
    python scripts/sync_availability_today.py --execute --fixture 1060362
    python scripts/sync_availability_today.py --execute --league 39 --no-lineups
    python scripts/sync_availability_today.py --execute --days 2 --max-requests 60
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: F401 — loads .env
from app.services.api_budget_service import can_run, get_daily_state, record_call

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger("sync_availability_today")

_SCRIPT_NAME = "sync_availability_today"


def _register_budget_hook() -> None:
    try:
        from app.data.api_football.client import register_call_hook

        def _hook(endpoint: str, duration_ms: int, status_code: int, results: int) -> None:
            record_call(
                endpoint=endpoint,
                duration_ms=duration_ms,
                status_code=status_code,
                results_count=results,
                source_script=_SCRIPT_NAME,
                priority="low",
            )

        register_call_hook(_hook)
    except Exception as exc:
        logger.debug("No se pudo registrar budget hook: %s", exc)


def _date_range(days: int) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days)
    return start.isoformat(), end.isoformat()


async def main(args: argparse.Namespace) -> None:
    now = datetime.now(timezone.utc)
    _register_budget_hook()

    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.data.supabase_reader import (
        get_fixtures_for_range, get_competition_season_map, get_team_provider_map,
    )
    from app.services.player_availability_service import player_availability_service

    print()
    print("=" * 60)
    print("  SYNC AVAILABILITY TODAY")
    print(f"  {now.strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"  Modo: {'DRY-RUN' if args.dry_run else 'EXECUTE'}")
    print("=" * 60)

    # Budget check
    state = get_daily_state()
    remaining = state.get("remaining")
    status = state.get("status", "unknown")
    if remaining is not None:
        print(f"  Cuota API: {remaining}/{state.get('limit', '?')} restantes [{status.upper()}]")
    else:
        print("  Cuota API: desconocida")

    if status == "critical" and not args.force:
        print("  !! Cuota CRITICA. Usa --force para continuar.")
        print()
        return

    if not can_run("low", allow_unknown=True):
        print("  Cuota insuficiente para prioridad 'low'. Usa --force para forzar.")
        if not args.force:
            print()
            return

    # Open DuckDB
    conn = get_local_db()
    init_schema(conn)

    # Resolve fixtures
    if args.fixture:
        # Single fixture mode — fetch metadata from Supabase
        from app.data.repositories.fixture_repo import get_fixture_by_provider_id
        fix = get_fixture_by_provider_id(args.fixture)
        if not fix:
            print(f"\n  Fixture {args.fixture} no encontrado en Supabase.")
            print("  Ejecuta sync_today.py primero.")
            return
        fixtures_raw = [fix]
        print(f"\n  Fixture único: {args.fixture}")
    else:
        start_utc, end_utc = _date_range(args.days)
        print(f"\n  Rango: {start_utc[:16]} -> {end_utc[:16]} UTC")
        fixtures_raw = get_fixtures_for_range(start_utc, end_utc, limit=min(args.limit or 200, 200))
        print(f"  Fixtures en Supabase: {len(fixtures_raw)}")

    if not fixtures_raw:
        print("\n  Sin fixtures. Ejecuta sync_today.py primero.")
        print()
        return

    # Build ID lookup maps
    all_fixture_ids = [f["id"] for f in fixtures_raw]
    team_ids_raw = list({
        t for f in fixtures_raw
        for t in (f.get("home_team_id"), f.get("away_team_id")) if t
    })

    cs_map = get_competition_season_map()         # league_id → {provider_league_id, season}
    team_map = get_team_provider_map(team_ids_raw) # team_id → {provider_team_id, name}

    # Filter by league if requested
    if args.league:
        target_provider_league = args.league
        filtered = []
        for f in fixtures_raw:
            meta = cs_map.get(f.get("league_id") or 0, {})
            if meta.get("provider_league_id") == target_provider_league:
                filtered.append(f)
        print(f"  Filtrado por liga {args.league}: {len(filtered)} fixtures")
        fixtures_raw = filtered

    if not fixtures_raw:
        print("  Sin fixtures tras filtrar.")
        print()
        return

    # Apply limit
    if args.limit:
        fixtures_raw = fixtures_raw[:args.limit]

    print(f"  Procesando: {len(fixtures_raw)} fixtures")
    print(f"  Injuries: {'sí' if not args.no_injuries else 'no'}  "
          f"Lineups: {'sí' if not args.no_lineups else 'no'}")

    calls_per_fixture = (0 if args.no_injuries else 1) + (0 if args.no_lineups else 1)
    print(f"  Llamadas estimadas: {len(fixtures_raw) * calls_per_fixture}")

    if args.max_requests and len(fixtures_raw) * calls_per_fixture > args.max_requests:
        max_fix = args.max_requests // max(calls_per_fixture, 1)
        print(f"  max-requests={args.max_requests} → limitando a {max_fix} fixtures")
        fixtures_raw = fixtures_raw[:max_fix]

    print()
    print("-" * 60)

    total_stats: dict[str, int] = {
        "fixtures_processed": 0, "injuries_found": 0,
        "lineups_found": 0, "api_calls": 0, "errors": 0,
        "skipped_no_provider_id": 0,
    }
    request_counter = [0]

    for f in fixtures_raw:
        provider_fixture_id = f.get("provider_fixture_id")
        if not provider_fixture_id:
            total_stats["skipped_no_provider_id"] += 1
            continue

        fixture_id = f.get("id")
        league_id_sb = f.get("league_id")
        meta = cs_map.get(league_id_sb or 0, {})
        provider_league_id = meta.get("provider_league_id")
        season = meta.get("season")

        home_sb_id = f.get("home_team_id")
        away_sb_id = f.get("away_team_id")
        home_info = team_map.get(home_sb_id or 0, {})
        away_info = team_map.get(away_sb_id or 0, {})
        home_provider_id = home_info.get("provider_team_id")
        away_provider_id = away_info.get("provider_team_id")

        if not home_provider_id or not away_provider_id:
            if args.verbose:
                print(f"  fixture={provider_fixture_id}: teams no encontrados en catálogo — skip")
            total_stats["skipped_no_provider_id"] += 1
            continue

        if args.verbose:
            home_name = home_info.get("name", str(home_provider_id))
            away_name = away_info.get("name", str(away_provider_id))
            print(f"  fixture={provider_fixture_id} {home_name} vs {away_name}")

        # Budget check per fixture
        if args.max_requests and request_counter[0] >= args.max_requests:
            print(f"  max-requests={args.max_requests} alcanzado — deteniendo.")
            break

        stats = await player_availability_service.sync_fixture(
            conn,
            provider_fixture_id=provider_fixture_id,
            home_team_id=home_provider_id,
            away_team_id=away_provider_id,
            fixture_id=fixture_id,
            league_id=provider_league_id,
            season=season,
            sync_injuries=not args.no_injuries,
            sync_lineups=not args.no_lineups,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )

        request_counter[0] += stats.get("api_calls", 0)
        total_stats["fixtures_processed"] += 1
        for k in ("injuries_found", "lineups_found", "api_calls", "errors"):
            total_stats[k] += stats.get(k, 0)

    print("-" * 60)
    print()
    print("  RESULTADO:")
    print(f"    Fixtures procesados:  {total_stats['fixtures_processed']}")
    print(f"    Bajas encontradas:    {total_stats['injuries_found']}")
    print(f"    Lineups encontrados:  {total_stats['lineups_found']}")
    print(f"    Llamadas API:         {total_stats['api_calls']}")
    print(f"    Errores:              {total_stats['errors']}")
    if total_stats["skipped_no_provider_id"]:
        print(f"    Saltados (sin ID):    {total_stats['skipped_no_provider_id']}")

    if args.dry_run:
        print()
        print("  DRY-RUN: sin cambios escritos. Usa --execute para aplicar.")

    print()
    print("=" * 60)
    print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sincroniza disponibilidad de jugadores (lesiones + alineaciones)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python scripts/sync_availability_today.py --dry-run --days 1 --limit 30\n"
            "  python scripts/sync_availability_today.py --execute --days 1 --limit 30\n"
            "  python scripts/sync_availability_today.py --execute --fixture 1060362\n"
            "  python scripts/sync_availability_today.py --execute --league 39 --max-requests 40\n"
        ),
    )
    parser.add_argument("--execute", dest="dry_run", action="store_false",
                        help="Aplicar cambios a DuckDB")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="Solo preview sin cambios (defecto)")
    parser.set_defaults(dry_run=True)
    parser.add_argument("--days", type=int, default=1, metavar="N",
                        help="Días hacia adelante desde hoy (defecto: 1)")
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Máximo de fixtures a procesar")
    parser.add_argument("--league", type=int, default=None, metavar="ID",
                        help="Solo fixtures de esta liga (provider_league_id)")
    parser.add_argument("--fixture", type=int, default=None, metavar="ID",
                        help="Fixture único por provider_fixture_id")
    parser.add_argument("--max-requests", type=int, default=None, metavar="N",
                        help="Máximo de llamadas API")
    parser.add_argument("--no-injuries", action="store_true",
                        help="No sincronizar lesiones")
    parser.add_argument("--no-lineups", action="store_true",
                        help="No sincronizar alineaciones")
    parser.add_argument("--force", action="store_true",
                        help="Continuar aunque la cuota esté en estado crítico o low")
    parser.add_argument("--verbose", action="store_true",
                        help="Mostrar detalle por fixture")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(args))
