#!/usr/bin/env python
"""Phase 3 — Historical ingest to local DuckDB.

Detects closed league/seasons in Supabase and archives them to
./data/local/football_history.duckdb, applying the configured rolling-window
pruning policy.

Closed-season signal:
  competition_seasons.current = False  AND
  >= HISTORY_MIN_TERMINAL_FRACTION of fixtures in terminal status (FT, AET, …)

Usage:
    python scripts/sync_historical.py               # archive all eligible
    python scripts/sync_historical.py --dry-run     # preview without writing
    python scripts/sync_historical.py --league 39   # one league only
    python scripts/sync_historical.py --league 39 --season 2024
    python scripts/sync_historical.py --dry-run --league 39
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.core.logger import setup_logger
    from app.services.history_sync_service import run_history_sync
except ImportError as e:
    print(f"Error de importación: {e}")
    sys.exit(1)


def _fmt_league(s: dict) -> str:
    return f"liga={s['provider_league_id']} ({s.get('league_name', '?')}) season={s['season']}"


def main() -> None:
    p = argparse.ArgumentParser(
        description="Phase 3 — Ingestión histórica a DuckDB local.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra qué haría sin ejecutar ningún cambio en DuckDB.",
    )
    p.add_argument(
        "--league",
        type=int,
        metavar="ID",
        help="Limitar a un provider_league_id específico.",
    )
    p.add_argument(
        "--season",
        type=int,
        metavar="YEAR",
        help="Limitar a un año de temporada (requiere --league).",
    )
    args = p.parse_args()

    if args.season and not args.league:
        p.error("--season requiere --league")

    setup_logger()

    tag = "[DRY-RUN] " if args.dry_run else ""
    print(f"\n{tag}=== sync_historical -- Fase 3 ===\n")

    result = run_history_sync(
        dry_run=args.dry_run,
        only_league=args.league,
        only_season=args.season,
    )

    eligible = result.get("eligible", [])
    archived = result.get("archived", [])
    skipped = result.get("skipped", [])
    pruned = result.get("pruned", [])

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"Elegibles  : {len(eligible)}")
    print(f"Archivadas : {len(archived)}")
    print(f"Omitidas   : {len(skipped)}  (ya en DuckDB)")
    print(f"Podadas    : {len(pruned)}")

    if archived:
        print(f"\n  {'[DRY-RUN] ' if args.dry_run else ''}Archivadas:")
        for s in archived:
            fix_n = s.get("fixtures", 0)
            pk_n = s.get("pick_results", 0)
            pp_n = s.get("published_picks", 0)
            print(
                f"    {_fmt_league(s)} — "
                f"fixtures={fix_n} pick_results={pk_n} published_picks={pp_n}"
            )

    if pruned:
        print(f"\n  {'[DRY-RUN] ' if args.dry_run else ''}Podadas (oldest season removed):")
        for pr in pruned:
            print(f"    liga={pr['provider_league_id']} season={pr['season']}")

    if skipped:
        print(f"\n  Omitidas (ya en DuckDB):")
        for s in skipped:
            print(f"    {_fmt_league(s)}")

    if not eligible:
        print("\nNo hay temporadas cerradas elegibles. Nada que archivar.")

    print()


if __name__ == "__main__":
    main()
