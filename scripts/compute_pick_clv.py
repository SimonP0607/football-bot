#!/usr/bin/env python
"""Phase 12: Compute CLV (Closing Line Value) for published picks.

Compares the odds at which picks were created against stored closing lines.
CLV > 0 means we obtained better odds than the closing line (positive EV signal).

Usage:
  python scripts/compute_pick_clv.py                # last 30 days of picks
  python scripts/compute_pick_clv.py --days 7       # last 7 days
  python scripts/compute_pick_clv.py --dry-run      # compute without writing
  python scripts/compute_pick_clv.py --report       # show CLV summary
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_scripts_dir = Path(__file__).resolve().parent
_src_root = _scripts_dir.parent
sys.path.insert(0, str(_src_root))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("compute_pick_clv")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compute CLV for published picks")
    p.add_argument("--days", type=int, default=30, help="Look back N days for picks (default: 30)")
    p.add_argument("--dry-run", action="store_true", help="Compute but do not write")
    p.add_argument("--report", action="store_true", help="Show aggregate CLV report after computing")
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return p.parse_args()


def _get_picks_for_clv(conn, days: int) -> list[dict]:
    """Return published picks with odds data for CLV computation."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        rows = conn.execute(
            """
            SELECT
                pc.id            AS pick_candidate_id,
                pp.id            AS published_pick_id,
                pc.fixture_id,
                pc.provider_fixture_id,
                pc.league_id,
                pc.market_key,
                pc.selection,
                pc.best_available_odd AS pick_odds,
                pc.bookmaker_name
            FROM pick_candidates pc
            LEFT JOIN published_picks pp ON pp.pick_candidate_id = pc.id
            WHERE pc.created_at >= ?
              AND pc.best_available_odd IS NOT NULL
              AND pc.best_available_odd > 1.0
            ORDER BY pc.created_at DESC
            """,
            [cutoff],
        ).fetchall()
        cols = [
            "pick_candidate_id", "published_pick_id", "fixture_id",
            "provider_fixture_id", "league_id", "market_key", "selection",
            "pick_odds", "bookmaker_name",
        ]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as exc:
        logger.warning("No se pudo consultar pick_candidates: %s", exc)
        return []


def main() -> None:
    args = _parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.services.market_intelligence_service import compute_pick_clv, get_clv_report

    conn = get_local_db()
    init_schema(conn)

    picks = _get_picks_for_clv(conn, args.days)
    print(f"Picks encontrados para CLV: {len(picks)} (últimos {args.days} día(s))")

    if not picks:
        print("No hay picks con odds disponibles.")
        if args.report:
            _show_report(conn, get_clv_report, args.days)
        return

    print(f"Dry-run: {args.dry_run}")

    result = compute_pick_clv(conn, picks, dry_run=args.dry_run)

    print(
        f"\nResultado:"
        f"\n  Picks procesados       : {result['total']}"
        f"\n  CLV computado          : {result['computed']}"
        f"\n  Sin closing line       : {result['no_closing_line']}"
        + (" [DRY-RUN]" if args.dry_run else "")
    )

    if args.report:
        _show_report(conn, get_clv_report, args.days)


def _show_report(conn, get_clv_report, days: int) -> None:
    summary = get_clv_report(conn, days=days)
    if not summary:
        print("\nNo hay datos de CLV aún.")
        return
    total = summary.get("total", 0)
    beat = summary.get("beat_count", 0)
    avg_clv = summary.get("avg_clv")
    print(
        f"\nResumen CLV ({days} días):"
        f"\n  Total picks con CLV    : {total}"
        f"\n  Beat closing line      : {beat} ({summary.get('beat_rate', 0)*100:.1f}%)"
        f"\n  CLV promedio           : {avg_clv*100:.2f}pp" if avg_clv is not None else
        f"\n  CLV promedio           : N/A"
    )
    print(
        f"  Positivo               : {summary.get('positive_count', 0)}"
        f"\n  Neutro                 : {summary.get('neutral_count', 0)}"
        f"\n  Negativo               : {summary.get('negative_count', 0)}"
        f"\n  Sin datos              : {summary.get('no_data_count', 0)}"
    )


if __name__ == "__main__":
    main()
