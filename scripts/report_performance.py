#!/usr/bin/env python
"""Performance report: hit rate, ROI, yield, and breakdown by market and league.

Uses Supabase pick_results as the source of truth.
Only picks with result_status IN ('win', 'loss', 'void') are included.

Usage:
    python scripts/report_performance.py              # all-time
    python scripts/report_performance.py --days 30    # last 30 days
    python scripts/report_performance.py --days 7     # last 7 days
    python scripts/report_performance.py --market OU25
    python scripts/report_performance.py --official-only   # picks in pick_results (all are official)
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: F401 -- loads .env
from app.data.repositories import settlement_repo
from app.data.repositories.supabase_client import get_supabase


def _get_league_map(fixture_ids: list[int]) -> dict[int, str]:
    """Return {fixture_id: league_name} for the given fixture IDs."""
    if not fixture_ids:
        return {}
    client = get_supabase()
    # fixtures.league_id -> competition_seasons.id -> competitions.name
    fix_rows = (
        client.table("fixtures")
        .select("id, league_id")
        .in_("id", fixture_ids)
        .execute()
    ).data or []

    cs_ids = list({f["league_id"] for f in fix_rows if f.get("league_id")})
    if not cs_ids:
        return {}

    cs_rows = (
        client.table("competition_seasons")
        .select("id, competition_id")
        .in_("id", cs_ids)
        .execute()
    ).data or []

    comp_ids = list({c["competition_id"] for c in cs_rows if c.get("competition_id")})
    comp_rows = (
        client.table("competitions")
        .select("id, name, country")
        .in_("id", comp_ids)
        .execute()
    ).data or []

    comp_map = {c["id"]: f"{c['name']} ({c.get('country', '?')})" for c in comp_rows}
    cs_map = {c["id"]: comp_map.get(c["competition_id"], "?") for c in cs_rows}
    fix_league = {f["id"]: cs_map.get(f["league_id"], "?") for f in fix_rows}
    return fix_league


def _print_stats(label: str, stats: dict) -> None:
    print(f"\n  {'=' * 54}")
    print(f"  {label}")
    print(f"  {'=' * 54}")
    if stats["total"] == 0:
        print("    Sin datos en este periodo.")
        return
    print(f"    Picks resueltos:  {stats['settled']:>4}  ({stats['voids']} anulados)")
    if not stats["settled"]:
        return
    print(f"    Ganados:          {stats['wins']:>4}")
    print(f"    Perdidos:         {stats['losses']:>4}")
    print(f"    Hit rate:         {stats['hit_rate_pct']:>5.1f}%")
    print(f"    Profit:           {stats['profit_units']:>+7.4f} u")
    print(f"    ROI / Yield:      {stats['roi_pct']:>+6.2f}%")

    if stats["settled"] < 20:
        print()
        print("    !  Muestra insuficiente para conclusiones estadisticas (< 20 picks).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reporte de rendimiento de picks")
    parser.add_argument("--days", type=int, default=None, metavar="N",
                        help="Limitar al periodo de N dias (por settled_at)")
    parser.add_argument("--market", metavar="MARKET", default=None,
                        help="Filtrar por mercado (1X2, OU25, BTTS)")
    parser.add_argument("--official-only", action="store_true",
                        help="Solo picks oficiales (todos los de pick_results lo son)")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    print()
    print("=" * 60)
    print("  REPORTE DE RENDIMIENTO")
    print(f"  {now.strftime('%Y-%m-%d %H:%M')} UTC")
    if args.days:
        print(f"  Periodo: ultimos {args.days} dias")
    else:
        print("  Periodo: historico completo")
    print("=" * 60)

    # -- All settled picks for requested period ---------------------------------
    rows = settlement_repo.get_settled(days=args.days, market=args.market)
    all_stats = settlement_repo.compute_performance_stats(rows)
    period_label = f"Ultimos {args.days} dias" if args.days else "Historico total"
    _print_stats(period_label, all_stats)

    if all_stats["settled"] == 0:
        print()
        print("  Sin picks resueltos.")
        print("  Ejecuta: python scripts/settle_results.py")
        print()
        return

    # -- Breakdown by market ----------------------------------------------------
    by_mkt = settlement_repo.get_stats_by_market(rows)
    if len(by_mkt) > 1:
        print(f"\n  {'Por mercado':=<54}")
        print(f"  {'Mercado':>8}  {'Resueltos':>9}  {'%Acierto':>9}  {'Profit':>9}  {'ROI':>7}")
        print("  " + "-" * 48)
        for mk, s in sorted(by_mkt.items()):
            if s["settled"]:
                print(
                    f"  {mk:>8}  {s['settled']:>9}  {s['hit_rate_pct']:>8.1f}%"
                    f"  {s['profit_units']:>+8.4f}u  {s['roi_pct']:>+6.2f}%"
                )

    # -- Breakdown by league ----------------------------------------------------
    fixture_ids = [r["fixture_id"] for r in rows]
    league_map = _get_league_map(fixture_ids)

    if league_map:
        by_league: dict[str, list] = {}
        for r in rows:
            league = league_map.get(r["fixture_id"], "Desconocida")
            by_league.setdefault(league, []).append(r)

        leagues_sorted = sorted(
            by_league.items(),
            key=lambda kv: settlement_repo.compute_performance_stats(kv[1])["settled"],
            reverse=True,
        )

        print(f"\n  {'Por liga (top 10)':=<54}")
        print(f"  {'Liga':<32}  {'Res':>3}  {'%Ac':>6}  {'Profit':>8}  {'ROI':>7}")
        print("  " + "-" * 60)
        for league, league_rows in leagues_sorted[:10]:
            s = settlement_repo.compute_performance_stats(league_rows)
            if s["settled"]:
                print(
                    f"  {league[:32]:<32}  {s['settled']:>3}"
                    f"  {s['hit_rate_pct']:>5.1f}%  {s['profit_units']:>+7.4f}u  {s['roi_pct']:>+6.2f}%"
                )

    # -- Comparison: last 7 vs last 30 vs all-time ------------------------------
    if not args.days:
        rows7  = settlement_repo.get_settled(days=7,  market=args.market)
        rows30 = settlement_repo.get_settled(days=30, market=args.market)
        s7  = settlement_repo.compute_performance_stats(rows7)
        s30 = settlement_repo.compute_performance_stats(rows30)

        print(f"\n  {'Comparativa temporal':=<54}")
        print(f"  {'Periodo':>12}  {'Picks':>5}  {'%Acierto':>9}  {'Profit':>9}  {'ROI':>7}")
        print("  " + "-" * 50)
        for label, s in [("7 dias", s7), ("30 dias", s30), ("Total", all_stats)]:
            if s["settled"]:
                print(
                    f"  {label:>12}  {s['settled']:>5}  {s['hit_rate_pct']:>8.1f}%"
                    f"  {s['profit_units']:>+8.4f}u  {s['roi_pct']:>+6.2f}%"
                )
            else:
                print(f"  {label:>12}  {'--':>5}  {'--':>9}  {'--':>9}  {'--':>7}")

    print()
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
