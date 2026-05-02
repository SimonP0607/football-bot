#!/usr/bin/env python
"""Sync prematch intelligence for upcoming fixtures (Phase 6).

Reads fixtures from Supabase, tracks odds movement vs stored opening,
refreshes injuries/lineups when within kickoff window, and generates alerts.
All writes go to DuckDB local tables — never touches Supabase or published picks.

Usage:
    python scripts/sync_prematch_intelligence.py --hours 6 --limit 50 --dry-run
    python scripts/sync_prematch_intelligence.py --hours 6 --limit 50 --execute
    python scripts/sync_prematch_intelligence.py --execute --fixture 1060362
    python scripts/sync_prematch_intelligence.py --execute --league 39 --hours 3
    python scripts/sync_prematch_intelligence.py --execute --no-lineups --max-requests 50
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: F401 — loads .env
from app.services.api_budget_service import can_run, get_daily_state, record_call

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger("sync_prematch_intelligence")

_SCRIPT_NAME = "sync_prematch_intelligence"


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


def main(args: argparse.Namespace) -> None:
    now = datetime.now(timezone.utc)
    _register_budget_hook()

    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.services.prematch_intelligence_service import run_prematch_intelligence

    print()
    print("=" * 64)
    print("  PREMATCH INTELLIGENCE SYNC (Phase 6)")
    print(f"  {now.strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"  Modo: {'DRY-RUN' if args.dry_run else 'EXECUTE'}")
    print(f"  Ventana: proximas {args.hours}h  |  Limite: {args.limit} fixtures")
    print("=" * 64)

    # Budget check
    state    = get_daily_state()
    remaining = state.get("remaining")
    status   = state.get("status", "unknown")
    if remaining is not None:
        print(f"  Cuota API: {remaining}/{state.get('limit', '?')} restantes [{status.upper()}]")
    if status == "critical" and not args.force:
        print("  !! Cuota CRITICA. Usa --force para continuar.")
        print()
        return
    if not can_run("medium", allow_unknown=True) and not args.force:
        print("  Cuota insuficiente para prioridad 'medium'. Usa --force para forzar.")
        print()
        return

    # Open DuckDB
    conn = get_local_db()
    init_schema(conn)

    print(f"\n  sync_odds:         {'si' if not args.no_odds else 'no'}")
    print(f"  sync_availability: {'si' if not args.no_availability else 'no'}")
    print(f"  sync_lineups:      {'si' if not args.no_lineups else 'no'}")
    if args.league:
        print(f"  Liga filtrada:     {args.league}")
    if args.fixture:
        print(f"  Fixture unico:     {args.fixture}")
    print()
    print("-" * 64)

    stats = run_prematch_intelligence(
        conn,
        hours=args.hours,
        limit=args.limit,
        execute=not args.dry_run,
        league_filter=args.league,
        fixture_filter=args.fixture,
        max_requests=args.max_requests,
        sync_odds=not args.no_odds,
        sync_availability=not args.no_availability,
        sync_lineups=not args.no_lineups,
        verbose=args.verbose,
    )

    print("-" * 64)
    print()
    print("  RESULTADO:")
    print(f"    Fixtures procesados:     {stats['fixtures_processed']}")
    print(f"    Odds rows procesadas:    {stats['odds_rows']}")
    print(f"    Alertas generadas:       {stats['alerts_created']}")
    print(f"    Lineups encontradas:     {stats['lineups_found']}")
    print(f"    Availability actualizadas: {stats['availability_updated']}")
    print(f"    Llamadas API usadas:     {stats['api_calls']}")
    print(f"    Errores:                 {stats['errors']}")

    if args.dry_run:
        print()
        print("  DRY-RUN: sin cambios escritos. Usa --execute para aplicar.")

    print()
    print("=" * 64)
    print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sincroniza inteligencia prematch para fixtures proximos",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python scripts/sync_prematch_intelligence.py --hours 6 --limit 50 --dry-run\n"
            "  python scripts/sync_prematch_intelligence.py --hours 6 --limit 50 --execute\n"
            "  python scripts/sync_prematch_intelligence.py --execute --fixture 1060362\n"
            "  python scripts/sync_prematch_intelligence.py --execute --league 39 --hours 3\n"
        ),
    )
    parser.add_argument("--execute", dest="dry_run", action="store_false",
                        help="Aplicar cambios a DuckDB")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="Solo preview sin cambios (defecto)")
    parser.set_defaults(dry_run=True)
    parser.add_argument("--hours", type=int, default=settings.prematch_refresh_hours, metavar="N",
                        help=f"Ventana de fixtures (horas, defecto: {settings.prematch_refresh_hours})")
    parser.add_argument("--limit", type=int, default=50, metavar="N",
                        help="Maximo de fixtures a procesar (defecto: 50)")
    parser.add_argument("--league", type=int, default=None, metavar="ID",
                        help="Solo fixtures de esta liga (provider_league_id)")
    parser.add_argument("--fixture", type=int, default=None, metavar="ID",
                        help="Fixture unico por provider_fixture_id")
    parser.add_argument("--max-requests", type=int, default=None, metavar="N",
                        help="Maximo de llamadas API para este script")
    parser.add_argument("--no-odds", action="store_true",
                        help="No procesar movimiento de cuotas")
    parser.add_argument("--no-availability", action="store_true",
                        help="No refrescar lesiones/disponibilidad")
    parser.add_argument("--no-lineups", action="store_true",
                        help="No sincronizar alineaciones")
    parser.add_argument("--force", action="store_true",
                        help="Continuar aunque la cuota este en estado critico")
    parser.add_argument("--verbose", action="store_true",
                        help="Mostrar detalle por fixture")
    return parser.parse_args()


if __name__ == "__main__":
    main(parse_args())
