#!/usr/bin/env python
"""Fase E — Gradear shadow picks contra resultados reales en fixtures_history.

Cruza shadow_value_picks.provider_fixture_id con fixtures_history (donde
fixtures_history.id = provider_fixture_id de API-Football). Si el score ya
está disponible, resuelve actual_outcome y model_correct. Si el partido no
terminó todavía, lo reporta como pending.

No conecta a Supabase. No toca producción. Solo lee/escribe DuckDB local.

Usage:
    python scripts/grade_shadow_picks.py              # gradear + reporte
    python scripts/grade_shadow_picks.py --report-only  # solo reporte sin gradar
    python scripts/grade_shadow_picks.py --pending      # solo mostrar picks pendientes

Exit codes:
    0  OK (incluso si 0 picks gradados)
    2  import error
"""

from __future__ import annotations

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
except ImportError as exc:
    print(f"Error de importacion: {exc}")
    sys.exit(2)


def _pct(v: float | None) -> str:
    return "  --  " if v is None else f"{v * 100:.1f}%"


def _print_pending(conn) -> None:
    """Show shadow picks that have a fixture_id but no score in fixtures_history yet."""
    rows = conn.execute(
        """
        SELECT
            svp.id,
            svp.run_date,
            svp.fixture_id,
            svp.provider_fixture_id,
            svp.provider_league_id,
            svp.market_key,
            svp.selection,
            svp.p_cal,
            svp.quality_score,
            svp.kickoff_at,
            CASE
                WHEN fh.id IS NOT NULL AND (fh.goals_home IS NULL OR fh.goals_away IS NULL)
                    THEN 'en_curso'
                WHEN fh.id IS NOT NULL
                    THEN 'tiene_score'   -- should not appear here (would have been graded)
                ELSE 'sin_historia'
            END AS pending_reason
        FROM shadow_value_picks svp
        LEFT JOIN fixtures_history fh
            ON fh.id = COALESCE(svp.provider_fixture_id, svp.fixture_id)
        WHERE svp.model_correct IS NULL
          AND (svp.provider_fixture_id IS NOT NULL OR svp.fixture_id IS NOT NULL)
        ORDER BY svp.run_date DESC, svp.fixture_id
        """
    ).fetchall()

    if not rows:
        print("\n  Sin picks pendientes de gradar.")
        return

    pending_reason_counts: dict[str, int] = {}
    for r in rows:
        pending_reason_counts[r[-1]] = pending_reason_counts.get(r[-1], 0) + 1

    print(f"\n  PICKS PENDIENTES ({len(rows)} total)")
    for reason, n in pending_reason_counts.items():
        print(f"    {reason}: {n}")

    print(f"\n  {'ID':>8}  {'Fecha':>10}  {'Fix':>8}  {'prov_fix':>8}  "
          f"{'Liga':>5}  {'Mkt':<5}  {'Sel':<12}  {'Razon'}")
    print(f"  {'-' * 72}")
    for r in rows[:40]:
        (pid, run_date, fid, pfid, lid, mkt, sel, pcal, qual, ko, reason) = r
        print(
            f"  {pid:>8}  {run_date:>10}  "
            f"{str(fid or '?'):>8}  {str(pfid or '?'):>8}  "
            f"{str(lid or '?'):>5}  {mkt:<5}  {sel:<12}  {reason}"
        )
    if len(rows) > 40:
        print(f"  ... y {len(rows) - 40} mas")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Fase E — Grade shadow picks vs fixtures_history (DuckDB local).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--report-only", action="store_true",
                   help="Solo mostrar resumen; no intentar gradar nuevos picks.")
    p.add_argument("--pending", action="store_true",
                   help="Mostrar picks pendientes con su razon (en_curso / sin_historia).")
    args = p.parse_args()

    setup_logger()
    logging.getLogger("app").setLevel(logging.WARNING)

    conn = get_local_db()

    # ── Grading ────────────────────────────────────────────────────────────────
    if not args.report_only:
        print(f"\n{'=' * 56}")
        print(f"  GRADING SHADOW PICKS")
        result = settle_shadow_picks(conn)
        print(f"  Picks con fixture_id : {result['total']}")
        print(f"  Gradados ahora       : {result['graded']}")
        print(f"  Pendientes (sin score): {result.get('pending', 0)}")
        print(f"  Sin historia local   : {result['no_score']}")
        print(f"{'=' * 56}")

        if result["no_score"] > 0:
            print(f"\n  NOTA: {result['no_score']} picks sin historia en DuckDB.")
            print(f"  Si son partidos recientes, ejecuta:")
            print(f"    python scripts/backfill_history_api.py --league <ID> --season <YEAR>")

    # ── Pending report ─────────────────────────────────────────────────────────
    if args.pending or (not args.report_only and not args.pending):
        # Show pending when --pending flag, or always after a grading pass
        if args.pending:
            _print_pending(conn)

    # ── Hit-rate summary ───────────────────────────────────────────────────────
    summary = shadow_picks_grade_summary(conn)

    if not summary:
        print(f"\n  Sin shadow picks gradados aun.")
        print(f"  Para activar grading real:")
        print(f"    1. python scripts/run_shadow_today.py --persist")
        print(f"    2. Esperar que los partidos terminen")
        print(f"    3. python scripts/backfill_history_api.py --league <ID> --season <YEAR>")
        print(f"    4. python scripts/grade_shadow_picks.py")
        if not args.pending:
            _print_pending(conn)
        return

    # Group by league
    leagues: dict[int | None, list[dict]] = {}
    for row in summary:
        leagues.setdefault(row["provider_league_id"], []).append(row)

    print(f"\n  HIT-RATE POR MERCADO")
    print(f"  {'Liga':>6}  {'Mercado':<6}  {'N':>5}  {'Correctos':>10}  {'Hit-Rate':>9}")
    print(f"  {'-' * 46}")

    for league_id in sorted(leagues.keys(), key=lambda x: (x is None, x)):
        rows_for_league = sorted(
            leagues[league_id],
            key=lambda r: MARKETS.index(r["market_key"]) if r["market_key"] in MARKETS else 99,
        )
        for row in rows_for_league:
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
    print(f"  {'-' * 46}")
    print(f"  {'TOTAL':>6}  {'--':<6}  {total_n:>5}  {total_c:>10}  {_pct(overall):>9}")
    print()


if __name__ == "__main__":
    main()
