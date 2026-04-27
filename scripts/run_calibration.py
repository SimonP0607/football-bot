#!/usr/bin/env python
"""Train and store probability calibrators from backtest training_samples.

Reads (model_probability, model_correct) pairs from training_samples,
bins them into a histogram, applies isotonic regression (PAVA), and
persists calibrators to calibration_registry in DuckDB.

Also populates market_quality_summary per league/market/season.

Prerequisite: run run_backtest.py first to populate training_samples.

Usage:
    python scripts/run_calibration.py --league 39
    python scripts/run_calibration.py --all-leagues
    python scripts/run_calibration.py --all-leagues --n-bins 20
    python scripts/run_calibration.py --league 39 --run-id 2

Exit codes:
    0  calibrators stored
    2  no training_samples found
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.core.logger import setup_logger
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.data.local import history_repo
    from app.services.calibration_service import train_and_store
    from app.services.backtest_service import MARKETS
except ImportError as e:
    print(f"Error de importacion: {e}")
    sys.exit(1)


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _print_summary(summaries: list[dict]) -> None:
    if not summaries:
        print("  (sin calibradores entrenados)")
        return

    has_val = any("val_ece" in s and s.get("val_ece") is not None for s in summaries)

    if has_val:
        header = (
            f"  {'Mercado':<6}  {'Scope':<8}  {'Liga':>6}  {'N':>5}  "
            f"{'ECE-train':>10}  {'ECE-val':>9}  {'val-Brier':>10}  {'val-LL':>8}"
        )
    else:
        header = (
            f"  {'Mercado':<6}  {'Scope':<8}  {'Liga':>6}  {'N':>5}  "
            f"{'ECE-antes':>10}  {'ECE-despues':>12}  {'Delta':>8}"
        )
    sep = "  " + "-" * (len(header) - 2)
    print(header)
    print(sep)

    for s in summaries:
        liga = str(s.get("provider_league_id") or "global")
        if has_val:
            val_ece    = s.get("val_ece")
            val_brier  = s.get("val_brier")
            val_logloss = s.get("val_logloss")
            ve_str  = f"{val_ece:.4f}"   if val_ece   is not None else "   --  "
            vb_str  = f"{val_brier:.4f}" if val_brier  is not None else "   --  "
            vll_str = f"{val_logloss:.4f}" if val_logloss is not None else "   --  "
            print(
                f"  {s['market_key']:<6}  {s['scope']:<8}  {liga:>6}  "
                f"{s['n_train']:>5}  "
                f"{s['ece_after']:>10.4f}  {ve_str:>9}  {vb_str:>10}  {vll_str:>8}"
            )
        else:
            delta = s["ece_after"] - s["ece_before"]
            print(
                f"  {s['market_key']:<6}  {s['scope']:<8}  {liga:>6}  "
                f"{s['n_train']:>5}  "
                f"{s['ece_before']:>10.4f}  {s['ece_after']:>12.4f}  "
                f"{delta:>+8.4f}"
            )
    print(sep)


def _print_market_quality(conn, league_ids: list[int]) -> None:
    """Print market_quality_summary for the calibrated leagues."""
    placeholders = ", ".join("?" * len(league_ids))
    rows = conn.execute(
        f"""
        SELECT provider_league_id, market_key, season, n, logloss, ece, eligible_flag
        FROM market_quality_summary
        WHERE provider_league_id IN ({placeholders})
        ORDER BY provider_league_id, market_key, season
        """,
        league_ids,
    ).fetchall()

    if not rows:
        return

    print(f"\n  CALIDAD POR MERCADO / TEMPORADA")
    print(
        f"  {'Liga':>6}  {'Mercado':<6}  {'Season':>7}  {'N':>5}  "
        f"{'LogLoss':>8}  {'ECE':>7}  {'Elegible'}"
    )
    print(f"  {'-' * 58}")
    for r in rows:
        eligible = "SI" if r[6] else "NO"
        ll_str = f"{r[4]:.4f}" if r[4] is not None else "  --  "
        ece_str = f"{r[5]:.4f}" if r[5] is not None else "  --  "
        print(
            f"  {r[0]:>6}  {r[1]:<6}  {r[2]:>7}  {r[3]:>5}  "
            f"{ll_str:>8}  {ece_str:>7}  {eligible}"
        )


def main() -> None:
    p = argparse.ArgumentParser(
        description="Calibra probabilidades del modelo desde training_samples.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--league", type=int, default=None, metavar="ID",
                   help="Liga especifica a calibrar (ej: 39 = Premier League).")
    p.add_argument("--all-leagues", action="store_true",
                   help="Calibrar todas las ligas en training_samples.")
    p.add_argument("--run-id", type=int, default=None, metavar="N",
                   help="Restringir a un backtest_run_id especifico.")
    p.add_argument("--n-bins", type=int, default=10, metavar="N",
                   help="Bins del histograma de calibracion (default: 10).")
    p.add_argument("--min-samples-league", type=int, default=50, metavar="N",
                   help="Minimo de muestras para calibrador por liga (default: 50).")
    p.add_argument("--min-samples-global", type=int, default=100, metavar="N",
                   help="Minimo de muestras para calibrador global (default: 100).")
    p.add_argument("--market", type=str, nargs="+", default=None, metavar="MKT",
                   help="Mercados especificos (ej: 1X2 OU25). Default: todos.")
    p.add_argument("--val-season", type=int, default=None, metavar="YEAR",
                   help="Temporada a usar como validacion out-of-sample (ej: 2024). "
                        "El calibrador se entrena en todas las OTRAS temporadas y se "
                        "evalua en esta para reportar ECE/Brier/LogLoss reales.")
    args = p.parse_args()

    if not args.league and not args.all_leagues:
        p.error("Especifica --league ID o --all-leagues.")

    setup_logger()
    logging.getLogger("app.services").setLevel(logging.INFO)
    logging.getLogger("app.data").setLevel(logging.WARNING)

    conn = get_local_db()
    init_schema(conn)

    if args.all_leagues:
        league_ids = history_repo.get_leagues_in_history(conn)
        if not league_ids:
            print("No hay ligas en fixtures_history. Ejecuta backfill_history_api.py primero.")
            sys.exit(2)
    else:
        league_ids = [args.league]

    # Validate training_samples exist
    placeholders = ", ".join("?" * len(league_ids))
    n_samples = conn.execute(
        f"SELECT COUNT(*) FROM training_samples WHERE provider_league_id IN ({placeholders})",
        league_ids,
    ).fetchone()[0]

    if n_samples == 0:
        print("No hay training_samples para las ligas indicadas.")
        print("Ejecuta run_backtest.py primero.")
        sys.exit(2)

    markets = args.market or None

    print(f"\n{'=' * 68}")
    print(f"  CALIBRACION DE PROBABILIDADES")
    print(f"  Ligas    : {league_ids}")
    print(f"  Mercados : {markets or list(MARKETS)}")
    print(f"  Muestras : {n_samples}  n_bins={args.n_bins}")
    if args.run_id:
        print(f"  Run ID   : {args.run_id}")
    if args.val_season:
        print(f"  Val Season: {args.val_season}  (held-out para ECE/Brier real)")
    print(f"{'=' * 68}\n")

    summaries = train_and_store(
        conn,
        league_ids=league_ids,
        markets=markets,
        run_id=args.run_id,
        n_bins=args.n_bins,
        min_samples_league=args.min_samples_league,
        min_samples_global=args.min_samples_global,
        val_season=args.val_season,
    )

    print(f"  {len(summaries)} calibradores entrenados\n")
    _print_summary(summaries)
    _print_market_quality(conn, league_ids)

    if not summaries:
        print(
            f"\n  Nota: sin muestras suficientes para calibrar.\n"
            f"  Minimo por liga: {args.min_samples_league}, global: {args.min_samples_global}\n"
            f"  Disponibles: {n_samples} total"
        )
        sys.exit(2)

    print(f"\n  Calibradores guardados en calibration_registry.")
    print(f"  Usa run_shadow_value.py para predecir con calibracion.\n")


if __name__ == "__main__":
    main()
