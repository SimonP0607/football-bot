#!/usr/bin/env python
"""Offline benchmark: compare model variants across leagues.

Runs walk-forward backtest for multiple leagues under three model variants:
  A) baseline   -- Elo + Poisson, flat season weighting (rho=0, regress=0)
  B) dc         -- + Dixon-Coles rho=-0.13, recent_weight=0.6
  C) dc+regress -- + seasonal Elo regression=0.1

All runs are dry_run=True (nothing written to DuckDB except competition_context
seeding, which is idempotent).

Club benchmarks  (ligas ricas en datos):
  Premier League (39), Serie A Italy (135), La Liga (140),
  Bundesliga (78), Championship (40), Serie A Brazil (71)

National team benchmarks:
  World Cup (1)           -- 4 seasons [2010-2022], 256 fixtures
  CONCACAF Gold Cup (22)  -- 3 seasons [2019-2023],  93 fixtures (small)

Usage:
    python scripts/bench_backtest.py
    python scripts/bench_backtest.py --train-seasons 2
    python scripts/bench_backtest.py --clubs-only
    python scripts/bench_backtest.py --nationals-only
    python scripts/bench_backtest.py --league 135 140 78
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.core.logger import setup_logger
    from app.services.backtest_service import run_backtest, MARKETS
except ImportError as e:
    print(f"Error de importacion: {e}")
    sys.exit(1)

# ── League groups ─────────────────────────────────────────────────────────────

CLUB_LEAGUES: list[tuple[int, str]] = [
    (39,  "Premier League"),
    (135, "Serie A (Italy)"),
    (140, "La Liga"),
    (78,  "Bundesliga"),
    (40,  "Championship"),
    (71,  "Serie A (Brazil)"),
]

NATIONAL_LEAGUES: list[tuple[int, str]] = [
    (1,  "World Cup"),
    (22, "CONCACAF Gold Cup"),
]

# ── Model variants ────────────────────────────────────────────────────────────

VARIANTS: list[dict] = [
    {
        "tag":              "baseline",
        "rho":              0.0,
        "elo_season_regress": 0.0,
        "recent_weight":    0.5,   # flat — same as original implementation
    },
    {
        "tag":              "dc",
        "rho":              -0.13,
        "elo_season_regress": 0.0,
        "recent_weight":    0.6,   # slight recency bias
    },
    {
        "tag":              "dc+regress",
        "rho":              -0.13,
        "elo_season_regress": 0.1,
        "recent_weight":    0.6,
    },
]


# ── Output helpers ────────────────────────────────────────────────────────────


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _fmt_ece(m: dict) -> str:
    calib = m.get("calibration", {})
    ece = calib.get("ece")
    return f"{ece:.4f}" if isinstance(ece, float) else "  --  "


def _run_variant(league_id: int, v: dict, train_seasons: int) -> dict | None:
    result = run_backtest(
        league_id,
        dry_run=True,
        train_seasons=train_seasons,
        rho=v["rho"],
        elo_season_regress=v["elo_season_regress"],
        recent_weight=v["recent_weight"],
    )
    if result.get("status") != "ok":
        return None
    return result


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("  (sin resultados)")
        return

    col_liga    = 22
    col_var     = 12
    w = (
        f"  {'Liga':<{col_liga}} {'Variante':<{col_var}} "
        f"{'N':>5}  "
        f"{'1X2-Acc':>8} {'1X2-LL':>7} {'1X2-ECE':>8}  "
        f"{'OU25-Acc':>9} {'BTTS-Acc':>9} {'DC-Acc':>7}"
    )
    sep = "  " + "-" * (len(w) - 2)
    print(w)
    print(sep)

    prev_liga = None
    for r in rows:
        liga = r["league_name"][:col_liga]
        if liga != prev_liga and prev_liga is not None:
            print(sep)
        prev_liga = liga

        mkt = r.get("metrics_by_market", {})
        m1  = mkt.get("1X2", {})
        mou = mkt.get("OU25", {})
        mbt = mkt.get("BTTS", {})
        mdc = mkt.get("DC", {})

        print(
            f"  {liga:<{col_liga}} {r['_variant']:<{col_var}} "
            f"{r.get('total_samples', 0):>5}  "
            f"{_pct(m1['accuracy']) if m1 else '--':>8} "
            f"{m1['log_loss']:>7.3f} " if m1 else f"{'--':>8} "
            f"{_fmt_ece(m1):>8}  "
            f"{_pct(mou['accuracy']) if mou else '--':>9} "
            f"{_pct(mbt['accuracy']) if mbt else '--':>9} "
            f"{_pct(mdc['accuracy']) if mdc else '--':>7}"
        )

    print(sep)


def _print_table_clean(rows: list[dict]) -> None:
    """A cleaner version that avoids f-string issues."""
    if not rows:
        print("  (sin resultados)")
        return

    header = (
        f"  {'Liga':<22} {'Variante':<12} {'N':>5}  "
        f"{'1X2-Acc':>8} {'1X2-LL':>7} {'1X2-ECE':>8}  "
        f"{'OU25':>6} {'BTTS':>6} {'DC':>6}"
    )
    sep = "  " + "-" * (len(header) - 2)
    print(header)
    print(sep)

    prev_liga = None
    for r in rows:
        liga = r["league_name"][:22]
        if liga != prev_liga and prev_liga is not None:
            print(sep)
        prev_liga = liga

        mkt = r.get("metrics_by_market", {})
        m1  = mkt.get("1X2", {})
        mou = mkt.get("OU25", {})
        mbt = mkt.get("BTTS", {})
        mdc = mkt.get("DC", {})

        acc1  = _pct(m1["accuracy"])  if m1  else "  -- "
        ll1   = f"{m1['log_loss']:.3f}" if m1 else " -- "
        ece1  = _fmt_ece(m1)
        accou = _pct(mou["accuracy"]) if mou else "  -- "
        accbt = _pct(mbt["accuracy"]) if mbt else "  -- "
        accdc = _pct(mdc["accuracy"]) if mdc else "  -- "
        n     = r.get("total_samples", 0)

        print(
            f"  {liga:<22} {r['_variant']:<12} {n:>5}  "
            f"{acc1:>8} {ll1:>7} {ece1:>8}  "
            f"{accou:>6} {accbt:>6} {accdc:>6}"
        )

    print(sep)


def bench_group(
    leagues: list[tuple[int, str]],
    variants: list[dict],
    train_seasons: int,
    label: str,
) -> list[dict]:
    print(f"\n{'=' * 76}")
    print(f"  BENCHMARK: {label}")
    print(f"  train_seasons={train_seasons}  variants={[v['tag'] for v in variants]}")
    print(f"  dry_run=True -- sin escritura en DuckDB")
    print(f"{'=' * 76}\n")

    collected: list[dict] = []
    for league_id, league_name in leagues:
        print(f"  [{league_id}] {league_name}")
        for v in variants:
            result = _run_variant(league_id, v, train_seasons)
            if result is None:
                status_msg = "sin datos suficientes"
                print(f"    [{v['tag']:<12}] {status_msg}")
                continue
            n = result.get("total_samples", 0)
            m1 = result.get("metrics_by_market", {}).get("1X2", {})
            acc = _pct(m1["accuracy"]) if m1 else "--"
            ll  = f"{m1['log_loss']:.3f}" if m1 else "--"
            print(f"    [{v['tag']:<12}] {n:>5} muestras  1X2: acc={acc} ll={ll}")
            result["_variant"] = v["tag"]
            collected.append(result)

    print()
    if collected:
        _print_table_clean(collected)

    return collected


def _delta_summary(rows: list[dict]) -> None:
    """Print improvement from baseline to best variant per league."""
    from collections import defaultdict
    by_liga: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_liga[r["league_name"]].append(r)

    has_delta = False
    for liga, variants in by_liga.items():
        base = next((v for v in variants if v["_variant"] == "baseline"), None)
        best = next((v for v in variants if v["_variant"] == "dc+regress"), None)
        if not base or not best:
            continue
        if not has_delta:
            print(f"\n  DELTA vs baseline (dc+regress - baseline):")
            print(f"  {'Liga':<22} {'1X2-Acc':>9} {'1X2-LL':>9} {'1X2-ECE':>10}")
            print(f"  {'-' * 52}")
            has_delta = True
        m_base = base.get("metrics_by_market", {}).get("1X2", {})
        m_best = best.get("metrics_by_market", {}).get("1X2", {})
        if not m_base or not m_best:
            continue
        d_acc = (m_best["accuracy"]   - m_base["accuracy"])   * 100
        d_ll  =  m_best["log_loss"]   - m_base["log_loss"]
        d_ece = (m_best.get("calibration", {}).get("ece", 0) -
                 m_base.get("calibration", {}).get("ece", 0))
        sign_acc = "+" if d_acc >= 0 else ""
        sign_ll  = "+" if d_ll  >= 0 else ""
        sign_ece = "+" if d_ece >= 0 else ""
        print(
            f"  {liga:<22} "
            f"{sign_acc}{d_acc:+.1f}pp{'':<2} "
            f"{sign_ll}{d_ll:+.3f}{'':<3} "
            f"{sign_ece}{d_ece:+.4f}"
        )


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    p = argparse.ArgumentParser(
        description="Benchmark comparativo de variantes del modelo.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--train-seasons", type=int, default=2, metavar="N",
                   help="Ventana de entrenamiento en temporadas (default: 2).")
    p.add_argument("--clubs-only", action="store_true",
                   help="Solo ligas de clubes.")
    p.add_argument("--nationals-only", action="store_true",
                   help="Solo selecciones.")
    p.add_argument("--league", type=int, nargs="+", metavar="ID",
                   help="Ligas especificas a incluir (reemplaza grupos predefinidos).")
    args = p.parse_args()

    setup_logger()
    # Keep logging quiet during bench runs
    logging.getLogger("app.services.backtest_service").setLevel(logging.WARNING)
    logging.getLogger("app.data.local").setLevel(logging.WARNING)

    all_results: list[dict] = []

    if args.league:
        custom = [(lid, f"Liga {lid}") for lid in args.league]
        all_results += bench_group(custom, VARIANTS, args.train_seasons, "Custom")
    else:
        if not args.nationals_only:
            all_results += bench_group(
                CLUB_LEAGUES, VARIANTS, args.train_seasons, "Ligas de Clubes"
            )
        if not args.clubs_only:
            all_results += bench_group(
                NATIONAL_LEAGUES, VARIANTS, args.train_seasons, "Selecciones"
            )

    if len(all_results) >= 2:
        _delta_summary(all_results)

    print()


if __name__ == "__main__":
    main()
