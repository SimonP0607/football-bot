#!/usr/bin/env python
"""Phase 4 -- Historical backfill from API-Football to local DuckDB.

Fetches fixtures, standings, and team statistics for a CLOSED league/season
from API-Football and stores them in ./data/local/football_history.duckdb.

Closed-season guard:
    The season must have current=False in API-Football (/leagues endpoint).
    If the season is still current, the script exits without writing anything.

Default API call budget (per season, no per-fixture extras):
    1  x /leagues          (coverage + current check)
    1  x /fixtures         (all season fixtures, one response)
    1  x /standings        (if coverage.standings)
    N  x /teams/statistics (one per unique team, typically 10-30)
    Total: ~13-33 calls for most leagues -- safe for the free plan.

With --with-per-fixture (optional):
    + N_fixtures x /fixtures/statistics   (if coverage)
    + N_fixtures x /injuries              (if coverage)
    + N_fixtures x /predictions           (if coverage)
    WARNING: 380-fixture season = up to 1140 extra calls.
    Combine with --limit-fixtures for testing.

Usage:
    python scripts/backfill_history_api.py --league 39 --season 2024
    python scripts/backfill_history_api.py --league 39 --season 2024 --dry-run
    python scripts/backfill_history_api.py --league 39 --season 2024 --limit-fixtures 5
    python scripts/backfill_history_api.py --league 39 --season 2024 --with-per-fixture
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.core.logger import setup_logger
    from app.services.history_backfill_service import run_backfill
except ImportError as e:
    print(f"Error de importacion: {e}")
    sys.exit(1)


def _fmt_val(v: object) -> str:
    if v == -1:
        return "N (dry-run)"
    return str(v)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Phase 4 -- Backfill historico desde API-Football a DuckDB local.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--league",
        type=int,
        required=True,
        metavar="ID",
        help="provider_league_id en API-Football (ej: 39 = Premier League).",
    )
    p.add_argument(
        "--season",
        type=int,
        required=True,
        metavar="YEAR",
        help="Ano de la temporada (ej: 2024). Debe ser una temporada cerrada.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra que se importaria sin escribir nada en DuckDB.",
    )
    p.add_argument(
        "--limit-fixtures",
        type=int,
        metavar="N",
        default=None,
        help="Procesar solo los primeros N fixtures. Util para pruebas rapidas.",
    )
    p.add_argument(
        "--with-per-fixture",
        action="store_true",
        help=(
            "Habilita llamadas por fixture: /fixtures/statistics, /injuries, /predictions. "
            "CARO: hasta ~3 llamadas extra por fixture. Usa --limit-fixtures para acotar."
        ),
    )
    args = p.parse_args()

    setup_logger()

    tag = "[DRY-RUN] " if args.dry_run else ""
    print(f"\n{tag}=== backfill_history_api -- Phase 4 ===")
    print(f"Liga   : {args.league}")
    print(f"Season : {args.season}")
    if args.limit_fixtures:
        print(f"Limite : {args.limit_fixtures} fixtures")
    if args.with_per_fixture:
        print("Modo   : con llamadas por fixture (injuries/predictions/stats)")
    print()

    result = asyncio.run(
        run_backfill(
            args.league,
            args.season,
            dry_run=args.dry_run,
            limit_fixtures=args.limit_fixtures,
            with_per_fixture=args.with_per_fixture,
        )
    )

    status = result.get("status", "ok")

    if status == "skipped_current":
        print("CANCELADO: La temporada tiene current=True en API-Football.")
        print("Solo se pueden importar temporadas cerradas.")
        sys.exit(2)

    if status == "not_found":
        print("ERROR: Liga/temporada no encontrada en API-Football.")
        sys.exit(3)

    if status == "empty":
        print("INFO: No hay fixtures para esta liga/temporada en API-Football.")
        sys.exit(0)

    skipped = result.get("skipped_endpoints", [])

    print(f"\n{'=' * 50}")
    print(f"  Liga              : {result['league_id']}")
    print(f"  Season            : {result['season']}")
    print(f"  Fixtures          : {_fmt_val(result['fixtures_inserted'])}")
    print(f"  Standings rows    : {_fmt_val(result['standings_rows'])}")
    print(f"  Team stats rows   : {_fmt_val(result['stats_rows'])}")
    print(f"  Injuries fetched  : {_fmt_val(result['injuries_fetched'])}")
    print(f"  Predictions fetch : {_fmt_val(result['predictions_fetched'])}")
    print(f"  Fixture stats     : {_fmt_val(result['fixture_stats_fetched'])}")
    if skipped:
        print(f"  Skipped (coverage): {', '.join(skipped)}")
    print(f"{'=' * 50}\n")

    if args.dry_run:
        print("DRY-RUN completado. Nada fue escrito en DuckDB.")
    else:
        print("Backfill completado. Ejecuta test_local_db.py para verificar.")


if __name__ == "__main__":
    main()
