#!/usr/bin/env python
"""Fase D — Reporte de shadow value picks desde DuckDB local.

Muestra picks recientes con métricas de calidad, edge/EV cuando existen,
y estado de grading. Completamente read-only.

Usage:
    python scripts/report_shadow_value.py
    python scripts/report_shadow_value.py --days 14
    python scripts/report_shadow_value.py --date 2025-05-01 --days 7
    python scripts/report_shadow_value.py --selected-only
    python scripts/report_shadow_value.py --with-odds-only

Exit codes:
    0  OK
    2  import error
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.core.logger import setup_logger
    from app.data.local.duckdb_client import get_local_db
    from app.services.backtest_service import MARKETS
except ImportError as exc:
    print(f"Error de importacion: {exc}")
    sys.exit(2)


# ── Formatters ─────────────────────────────────────────────────────────────────


def _pct(v: float | None) -> str:
    return "  -- " if v is None else f"{v * 100:.1f}%"


def _o(v: float | None) -> str:
    return "  --" if v is None else f"{v:.2f}"


def _ev(v: float | None) -> str:
    return "   -- " if v is None else f"{v:+.4f}"


# ── Query ──────────────────────────────────────────────────────────────────────


_COLS = [
    "id", "run_date", "fixture_id", "provider_fixture_id",
    "provider_league_id", "league_name",
    "market_key", "selection",
    "p_raw", "p_cal", "fair_odds", "offered_odds",
    "edge", "ev", "ev_adj", "w_rel", "quality_score",
    "decision_status",
    "actual_outcome", "model_correct", "graded_at",
    "kickoff_at", "home_provider_team_id", "away_provider_team_id",
]


def _load_picks(conn, start_date: str, end_date: str, args) -> list[dict]:
    filters = ["run_date >= ? AND run_date <= ?"]
    params: list = [start_date, end_date]

    if args.selected_only:
        filters.append("decision_status = 'selected'")
    if args.with_odds_only:
        filters.append("offered_odds IS NOT NULL")

    where = " AND ".join(filters)
    rows = conn.execute(
        f"SELECT {', '.join(_COLS)} FROM shadow_value_picks WHERE {where} "
        f"ORDER BY run_date DESC, fixture_id, market_key",
        params,
    ).fetchall()
    return [dict(zip(_COLS, r)) for r in rows]


# ── Section printers ───────────────────────────────────────────────────────────


def _print_overview(data: list[dict]) -> None:
    selected  = [d for d in data if d["decision_status"] == "selected"]
    with_odds = [d for d in data if d["offered_odds"] is not None]
    graded    = [d for d in data if d["model_correct"] is not None]
    correct   = [d for d in graded if d["model_correct"]]
    gradable  = [d for d in data if d["fixture_id"] is not None and d["model_correct"] is None]

    hit = round(len(correct) / len(graded), 4) if graded else None

    print(f"  Total picks          : {len(data)}")
    print(f"  Seleccionados        : {len(selected)}")
    print(f"  Con odds/edge        : {len(with_odds)}")
    print(f"  Gradados             : {len(graded)}  (correctos={len(correct)}  hit={_pct(hit)})")
    print(f"  Pendientes de gradar : {len(gradable)}")


def _print_by_market(selected: list[dict]) -> None:
    if not selected:
        print(f"\n  (sin picks seleccionados)")
        return

    print(f"\n  POR MERCADO (seleccionados)")
    print(f"  {'Mkt':<6}  {'N':>4}  {'Avg p_cal':>10}  {'Con odds':>8}  {'Avg edge':>9}  {'Grad':>5}  {'Hit':>7}")
    print(f"  {'-' * 58}")

    for mkt in MARKETS:
        ms = [d for d in selected if d["market_key"] == mkt]
        if not ms:
            continue
        avg_pcal = sum(d["p_cal"] for d in ms if d["p_cal"]) / len(ms)
        with_e   = [d for d in ms if d["edge"] is not None]
        avg_edge = sum(d["edge"] for d in with_e) / len(with_e) if with_e else None
        grad     = [d for d in ms if d["model_correct"] is not None]
        corr     = [d for d in grad if d["model_correct"]]
        hit      = round(len(corr) / len(grad), 4) if grad else None
        print(
            f"  {mkt:<6}  {len(ms):>4}  {_pct(avg_pcal):>10}  "
            f"{len(with_e):>8}  {_ev(avg_edge):>9}  {len(grad):>5}  {_pct(hit):>7}"
        )


def _print_by_league(selected: list[dict]) -> None:
    if not selected:
        return
    from collections import Counter
    league_counts: dict = {}
    for d in selected:
        lid  = d["provider_league_id"]
        name = d["league_name"] or str(lid)
        key  = (lid, name)
        league_counts.setdefault(key, []).append(d)

    print(f"\n  POR LIGA (seleccionados, top 15)")
    print(f"  {'Liga':>5}  {'Nombre':<28}  {'N':>4}  {'Con odds':>8}  {'Grad':>5}  {'Hit':>7}")
    print(f"  {'-' * 60}")
    for (lid, name), rows in sorted(league_counts.items(), key=lambda x: -len(x[1]))[:15]:
        with_e = [d for d in rows if d["edge"] is not None]
        grad   = [d for d in rows if d["model_correct"] is not None]
        corr   = [d for d in grad if d["model_correct"]]
        hit    = round(len(corr) / len(grad), 4) if grad else None
        print(
            f"  {str(lid or '?'):>5}  {str(name)[:28]:<28}  {len(rows):>4}  "
            f"{len(with_e):>8}  {len(grad):>5}  {_pct(hit):>7}"
        )


def _print_picks_table(picks: list[dict], sort_col: str, label: str) -> None:
    if not picks:
        return
    print(f"\n  {label}")
    header = (
        f"  {'ID':>8}  {'Date':>10}  {'Liga':>5}  {'Fix':>8}  {'Mkt':<5}  "
        f"{'Seleccion':<12}  {'p_cal':>6}  {'fair':>6}  {'odds':>6}  {'edge':>7}  {sort_col}"
    )
    print(header)
    print(f"  {'-' * (len(header) - 2)}")
    for d in picks:
        val     = d.get(sort_col)
        val_str = f"{val:+.4f}" if isinstance(val, float) else (str(val) if val is not None else "  --  ")
        print(
            f"  {d['id']:>8}  {d['run_date']:>10}  "
            f"{str(d['provider_league_id'] or '?'):>5}  "
            f"{str(d['fixture_id'] or '?'):>8}  "
            f"{d['market_key']:<5}  {d['selection']:<12}  "
            f"{_pct(d['p_cal']):>6}  {_o(d['fair_odds']):>6}  "
            f"{_o(d.get('offered_odds')):>6}  "
            f"{_ev(d.get('edge')):>7}  "
            f"{val_str}"
        )


def _print_gradable(gradable: list[dict]) -> None:
    if not gradable:
        return
    print(f"\n  PICKS GRADABLES (fixture_id real, sin resultado aun: {len(gradable)})")
    print(f"  {'ID':>8}  {'Date':>10}  {'Fix':>8}  {'prov_fix':>8}  {'Mkt':<5}  {'Sel':<12}  {'p_cal':>6}  {'Kickoff':>16}")
    print(f"  {'-' * 74}")
    for d in sorted(gradable, key=lambda x: x["run_date"], reverse=True)[:30]:
        ko = (d["kickoff_at"] or "")[:16].replace("T", " ")
        print(
            f"  {d['id']:>8}  {d['run_date']:>10}  "
            f"{str(d['fixture_id'] or '?'):>8}  "
            f"{str(d['provider_fixture_id'] or '?'):>8}  "
            f"{d['market_key']:<5}  {d['selection']:<12}  "
            f"{_pct(d['p_cal']):>6}  {ko:>16}"
        )
    if len(gradable) > 30:
        print(f"  ... y {len(gradable) - 30} mas")


def _print_graded(graded: list[dict]) -> None:
    if not graded:
        return
    correct = [d for d in graded if d["model_correct"]]
    print(f"\n  PICKS GRADADOS ({len(graded)} total, {len(correct)} correctos)")
    print(f"  {'ID':>8}  {'Date':>10}  {'Mkt':<5}  {'Sel':<12}  {'Actual':<12}  {'OK':>4}  {'Gradado'}")
    print(f"  {'-' * 64}")
    for d in sorted(graded, key=lambda x: x["graded_at"] or "", reverse=True)[:20]:
        ok_str = "SI" if d["model_correct"] else "NO"
        print(
            f"  {d['id']:>8}  {d['run_date']:>10}  "
            f"{d['market_key']:<5}  {d['selection']:<12}  "
            f"{str(d['actual_outcome'] or '?'):<12}  {ok_str:>4}  "
            f"{d['graded_at'] or '?'}"
        )


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    p = argparse.ArgumentParser(
        description="Fase D — Reporte de shadow value picks (DuckDB local, read-only).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--date", type=str, default=None, metavar="YYYY-MM-DD",
                   help="Inicio del periodo a reportar (default: hoy - days).")
    p.add_argument("--days", type=int, default=7, metavar="N",
                   help="Dias hacia atras a incluir (default: 7).")
    p.add_argument("--selected-only", action="store_true",
                   help="Solo mostrar picks con decision_status='selected'.")
    p.add_argument("--with-odds-only", action="store_true",
                   help="Solo mostrar picks que tienen offered_odds (edge/EV activos).")
    args = p.parse_args()

    setup_logger()
    logging.getLogger("app").setLevel(logging.WARNING)

    conn = get_local_db()

    end_date   = str(date.today())
    start_date = args.date if args.date else str(date.today() - timedelta(days=args.days - 1))

    data = _load_picks(conn, start_date, end_date, args)

    print(f"\n{'=' * 76}")
    print(f"  SHADOW VALUE PICKS  {start_date} -> {end_date}")
    if args.selected_only or args.with_odds_only:
        flags = []
        if args.selected_only:
            flags.append("solo seleccionados")
        if args.with_odds_only:
            flags.append("solo con odds")
        print(f"  Filtros: {', '.join(flags)}")
    print(f"{'=' * 76}")

    if not data:
        print(f"\n  Sin shadow picks para el periodo indicado.")
        print(f"  Ejecuta: python scripts/run_shadow_today.py --persist")
        return

    _print_overview(data)

    selected  = [d for d in data if d["decision_status"] == "selected"]
    with_ev   = [d for d in selected if d["ev_adj"] is not None]
    gradable  = [d for d in data if d["fixture_id"] is not None and d["model_correct"] is None]
    graded    = [d for d in data if d["model_correct"] is not None]

    _print_by_market(selected)
    _print_by_league(selected)

    # Top picks por quality_score
    top_quality = sorted(selected, key=lambda d: d["quality_score"] or 0, reverse=True)[:10]
    _print_picks_table(top_quality, "quality_score", "TOP PICKS POR quality_score (seleccionados)")

    # Top picks por ev_adj (solo cuando existen)
    if with_ev:
        top_ev = sorted(with_ev, key=lambda d: d["ev_adj"] or 0, reverse=True)[:10]
        _print_picks_table(top_ev, "ev_adj", "TOP PICKS POR ev_adj (con odds)")

    _print_gradable(gradable)
    _print_graded(graded)

    print()


if __name__ == "__main__":
    main()
