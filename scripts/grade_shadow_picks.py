#!/usr/bin/env python
"""Grade shadow picks by cross-referencing completed fixture scores.

Resolves actual_outcome + model_correct for all ungraded shadow_value_picks
that have a valid fixture_id and a completed score in fixtures_history.

After running run_shadow_value.py for several fixtures, run this script
to see real hit-rate by market and league.

Usage:
    python scripts/grade_shadow_picks.py              # grade + report
    python scripts/grade_shadow_picks.py --report-only  # only show summary

Exit codes:
    0  OK (even if 0 picks graded)
    2  import error
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.core.logger import setup_logger
    from app.data.local.duckdb_client import get_local_db
    from app.services.value_betting_service import (
        settle_shadow_picks,
        shadow_picks_grade_summary,
    )
    from app.services.backtest_service import MARKETS
except ImportError as e:
    print(f"Error de importacion: {e}")
    sys.exit(2)


def _pct(v: float | None) -> str:
    if v is None:
        return "  --  "
    return f"{v * 100:.1f}%"


def main() -> None:
    p = argparse.ArgumentParser(
        description="Grade shadow picks against completed fixture scores.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--report-only", action="store_true",
        help="Solo mostrar resumen sin intentar gradar nuevos picks.",
    )
    args = p.parse_args()

    setup_logger()
    logging.getLogger("app").setLevel(logging.WARNING)

    conn = get_local_db()

    if not args.report_only:
        print(f"\n{'=' * 52}")
        print(f"  GRADING SHADOW PICKS")
        result = settle_shadow_picks(conn)
        print(f"  Picks evaluados : {result['total']}")
        print(f"  Gradados        : {result['graded']}")
        print(f"  Sin score aun   : {result['no_score']}")
        print(f"{'=' * 52}")

    summary = shadow_picks_grade_summary(conn)
    if not summary:
        print("\n  No hay shadow picks gradados aun.")
        print("  Ejecuta run_shadow_value.py con --fixture-id real,")
        print("  espera que el partido termine, y vuelve a correr este script.")
        return

    # Group by league for cleaner output
    leagues: dict[int | None, list[dict]] = {}
    for row in summary:
        leagues.setdefault(row["provider_league_id"], []).append(row)

    print(f"\n  HIT-RATE POR MERCADO")
    print(f"  {'Liga':>6}  {'Mercado':<6}  {'N':>5}  {'Correctos':>10}  {'Hit-Rate':>9}")
    print(f"  {'-' * 44}")

    for league_id in sorted(leagues.keys(), key=lambda x: (x is None, x)):
        for row in sorted(leagues[league_id], key=lambda r: MARKETS.index(r["market_key"]) if r["market_key"] in MARKETS else 99):
            liga_str = str(league_id) if league_id is not None else "global"
            print(
                f"  {liga_str:>6}  {row['market_key']:<6}  "
                f"{row['n_graded']:>5}  {row['n_correct']:>10}  "
                f"{_pct(row['hit_rate']):>9}"
            )

    # Overall
    total_n = sum(r["n_graded"] for r in summary)
    total_c = sum(r["n_correct"] for r in summary)
    overall = round(total_c / total_n, 4) if total_n else None
    print(f"  {'-' * 44}")
    print(f"  {'TOTAL':>6}  {'--':<6}  {total_n:>5}  {total_c:>10}  {_pct(overall):>9}")
    print()


if __name__ == "__main__":
    main()
