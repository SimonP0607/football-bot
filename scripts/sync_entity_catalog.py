#!/usr/bin/env python
"""Sync the entity catalog: teams, squad memberships, and basic player identity.

Reads active leagues from tracked_competitions (Supabase), fetches teams from
API-Football, and writes to the local DuckDB entity catalog tables.

Budget: every API call is logged via api_budget_service. Phases are blocked
automatically when quota is low.

Usage:
    python scripts/sync_entity_catalog.py --dry-run --limit 5
    python scripts/sync_entity_catalog.py --execute --tier 1 --include-squads --max-requests 500
    python scripts/sync_entity_catalog.py --execute --league 39 --season 2025 --include-squads
    python scripts/sync_entity_catalog.py --execute --national-only --include-squads --max-requests 50
    python scripts/sync_entity_catalog.py --execute --only-teams --limit 20 --max-requests 20
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: F401 -- loads .env
from app.services.api_budget_service import get_daily_state, record_call

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger("sync_entity_catalog")

_SCRIPT_NAME = "sync_entity_catalog"


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
                priority="medium",
            )

        register_call_hook(_hook)
    except Exception as exc:
        logger.debug("No se pudo registrar budget hook: %s", exc)


async def main(args: argparse.Namespace) -> None:
    now = datetime.now(timezone.utc)
    _register_budget_hook()

    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.data.local.entity_repo import (
        create_entity_sync_run,
        finish_entity_sync_run,
        entity_counts,
    )
    from app.services.entity_catalog_service import entity_catalog_service

    print()
    print("=" * 60)
    print("  SYNC ENTITY CATALOG")
    print(f"  {now.strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"  Modo: {'DRY-RUN' if args.dry_run else 'EXECUTE'}")
    print("=" * 60)

    # Budget state
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

    # Open DuckDB and ensure schema is ready
    conn = get_local_db()
    init_schema(conn)

    counts_before = entity_counts(conn)
    print(f"\n  Estado previo:")
    print(f"    Equipos:       {counts_before.get('team_identity', 0)}")
    print(f"    Jugadores:     {counts_before.get('player_identity', 0)}")
    print(f"    Memberships:   {counts_before.get('team_season_membership', 0)}")
    print(f"    Squad entries: {counts_before.get('squad_membership', 0)}")

    # Create audit run
    run_id = -1
    if not args.dry_run:
        run_id = create_entity_sync_run(conn, mode="execute", leagues_requested=0)

    print()
    print("  Sincronizando ligas...")
    print("-" * 60)

    try:
        result = await entity_catalog_service.sync_all_leagues(
            conn,
            league_filter=args.league,
            tier_filter=args.tier,
            season_override=args.season,
            include_squads=args.include_squads,
            only_teams=args.only_teams,
            only_squads=args.only_squads,
            national_only=args.national_only,
            club_only=args.club_only,
            dry_run=args.dry_run,
            max_requests=args.max_requests,
            limit=args.limit,
            verbose=args.verbose,
        )
    except Exception as exc:
        logger.exception("sync_all_leagues failed: %s", exc)
        if run_id >= 0:
            finish_entity_sync_run(conn, run_id, status="failed", error_message=str(exc))
        print(f"\n  ERROR: {exc}")
        return

    print("-" * 60)
    print()
    print("  RESULTADO:")
    print(f"    Ligas procesadas:    {result.get('leagues_requested', 0)}")
    print(f"    Equipos encontrados: {result.get('teams_found', 0)}")
    print(f"    Equipos escritos:    {result.get('teams_written', 0)}")
    print(f"    Memberships:         {result.get('memberships_written', 0)}")
    print(f"    Jugadores (squad):   {result.get('players_written', 0)}")
    print(f"    Squad entries:       {result.get('squads_written', 0)}")
    print(f"    Llamadas API:        {result.get('api_calls', 0)}")
    print(f"    Errores:             {result.get('errors', 0)}")

    if not args.dry_run:
        counts_after = entity_counts(conn)
        print()
        print("  Estado actualizado:")
        print(f"    Equipos:       {counts_after.get('team_identity', 0)}")
        print(f"    Jugadores:     {counts_after.get('player_identity', 0)}")
        print(f"    Memberships:   {counts_after.get('team_season_membership', 0)}")
        print(f"    Squad entries: {counts_after.get('squad_membership', 0)}")

        finish_entity_sync_run(
            conn, run_id,
            status="completed",
            teams_synced=result.get("teams_written", 0),
            memberships_synced=result.get("memberships_written", 0),
            players_synced=result.get("players_written", 0),
            squads_synced=result.get("squads_written", 0),
            api_calls=result.get("api_calls", 0),
        )
    else:
        print()
        print("  DRY-RUN: sin cambios escritos. Usa --execute para aplicar.")

    print()
    print("=" * 60)
    print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sincroniza el catalogo de entidades deportivas (equipos, plantillas)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python scripts/sync_entity_catalog.py --dry-run --limit 5\n"
            "  python scripts/sync_entity_catalog.py --execute --tier 1 --include-squads\n"
            "  python scripts/sync_entity_catalog.py --execute --league 39 --season 2025\n"
            "  python scripts/sync_entity_catalog.py --execute --national-only --include-squads\n"
        ),
    )
    parser.add_argument("--execute", dest="dry_run", action="store_false",
                        help="Aplicar cambios a Supabase/DuckDB")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="Solo preview sin cambios (defecto)")
    parser.set_defaults(dry_run=True)
    parser.add_argument("--league", type=int, metavar="ID",
                        help="Solo sincronizar esta liga (provider_league_id)")
    parser.add_argument("--tier", type=int, choices=[1, 2, 3], metavar="N",
                        help="Solo ligas de este tier (1, 2, o 3)")
    parser.add_argument("--season", type=int, metavar="YEAR",
                        help="Override de temporada para todas las ligas")
    parser.add_argument("--include-squads", action="store_true",
                        help="Descargar plantillas basicas (/players/squads)")
    parser.add_argument("--only-teams", action="store_true",
                        help="Solo equipos, sin plantillas")
    parser.add_argument("--only-squads", action="store_true",
                        help="Solo plantillas (asume que los equipos ya estan)")
    parser.add_argument("--national-only", action="store_true",
                        help="Solo selecciones nacionales")
    parser.add_argument("--club-only", action="store_true",
                        help="Solo clubes (excluye selecciones)")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="Maximo de ligas a procesar")
    parser.add_argument("--max-requests", type=int, metavar="N",
                        help="Maximo de llamadas API")
    parser.add_argument("--force", action="store_true",
                        help="Ignorar estado CRITICO de cuota")
    parser.add_argument("--verbose", action="store_true",
                        help="Mostrar detalle de cada equipo/jugador")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(args))
