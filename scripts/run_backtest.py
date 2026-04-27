#!/usr/bin/env python
"""Phase 5 -- Walk-forward Poisson + Elo backtest.

Evaluates model calibration using historical fixtures already loaded in
DuckDB (via init_local_db.py + backfill_history_api.py).

No production code is touched. Does not modify Supabase.

Prerequisite: run  python scripts/init_local_db.py  first to apply
the 002_backtest_schema.sql migration.

Usage:
    python scripts/run_backtest.py --league 39
    python scripts/run_backtest.py --league 39 --season 2023
    python scripts/run_backtest.py --league 39 --train-seasons 4 --dry-run
    python scripts/run_backtest.py --league 39 --rho -0.13
    python scripts/run_backtest.py --league 39 --elo-regress 0.1
    python scripts/run_backtest.py --all-leagues

Exit codes:
    0  completed successfully (or dry-run)
    2  no data for league/season
    3  insufficient seasons for the requested training window
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.core.logger import setup_logger
    from app.data.local.duckdb_client import get_local_db
    from app.data.local import history_repo
    from app.services.backtest_service import run_backtest, seed_competition_context, MARKETS
except ImportError as e:
    print(f"Error de importacion: {e}")
    sys.exit(1)


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _print_report(result: dict) -> None:
    tag = " [DRY-RUN]" if result.get("dry_run") else ""
    rho = result.get("rho", 0.0)
    regress = result.get("elo_season_regress", 0.0)
    cap = result.get("max_daily_picks", "N")

    print(f"\n{'=' * 60}")
    print(f"  BACKTEST REPORT{tag}")
    print(f"  Liga    : {result['league_id']} ({result['league_name']})")
    print(f"  Scope   : {result['entity_scope']}")
    print(f"  Test    : {result['test_seasons']}")
    print(f"  Train   : {result['train_seasons']} temporadas previas")
    print(f"  Muestras: {result['total_samples']}")
    print(f"  Params  : rho={rho}  elo_regress={regress}  cap/dia={cap}")
    if result.get("run_id"):
        print(f"  Run ID  : {result['run_id']}")
    print(f"{'=' * 60}")

    mkt = result.get("metrics_by_market", {})
    sel = result.get("selected_picks", {})

    if mkt:
        print("\n  CALIBRACION (todos los candidatos)")
        print(f"  {'Mercado':<6}  {'N':>5}  {'Accuracy':>9}  {'Brier':>7}  {'LogLoss':>8}  {'ECE':>6}")
        print(f"  {'-' * 50}")
        for market in MARKETS:
            m = mkt.get(market)
            if not m:
                continue
            ece = m.get("calibration", {}).get("ece", "—")
            ece_str = f"{ece:.4f}" if isinstance(ece, float) else str(ece)
            print(
                f"  {market:<6}  {m['n']:>5}  "
                f"{_pct(m['accuracy']):>9}  "
                f"{m['brier_score']:>7.4f}  "
                f"{m['log_loss']:>8.4f}  "
                f"{ece_str:>6}"
            )

    if sel:
        print(f"\n  PICKS SELECCIONADOS (cap={cap} por dia)")
        print(f"  {'Mercado':<6}  {'N':>5}  {'Wins':>5}  {'Winrate':>8}  {'P/L (fair)':>11}  {'ROI':>7}")
        print(f"  {'-' * 48}")
        for market in MARKETS:
            s = sel.get(market)
            if not s:
                continue
            fair = mkt.get(market, {}).get("fair_odds", {})
            pl = fair.get("profit_units", 0.0)
            roi = fair.get("roi_pct", 0.0)
            print(
                f"  {market:<6}  {s['n']:>5}  "
                f"{s['wins']:>5}  "
                f"{_pct(s['winrate']):>8}  "
                f"{pl:>+11.2f}  "
                f"{roi:>+6.1f}%"
            )

    # Per-season breakdown
    by_season = result.get("metrics_by_season", {})
    if by_season:
        print(f"\n  POR TEMPORADA (todos los mercados)")
        print(f"  {'Season':>6}  {'N':>5}  {'Accuracy':>9}  {'Brier':>7}  {'LogLoss':>8}")
        print(f"  {'-' * 45}")
        for season, sm in sorted(by_season.items()):
            if not sm:
                continue
            print(
                f"  {season:>6}  {sm['n']:>5}  "
                f"{_pct(sm['accuracy']):>9}  "
                f"{sm['brier_score']:>7.4f}  "
                f"{sm['log_loss']:>8.4f}"
            )

    # Calibration buckets for 1X2 (most informative)
    calib_1x2 = mkt.get("1X2", {}).get("calibration", {})
    buckets = calib_1x2.get("buckets", [])
    if any(b.get("n", 0) > 0 for b in buckets):
        print(f"\n  CALIBRACION 1X2 por bucket de probabilidad")
        print(f"  {'Rango':<12}  {'N':>5}  {'Mean Pred':>10}  {'Actual Rate':>12}  {'Error':>7}")
        print(f"  {'-' * 50}")
        for b in buckets:
            if b.get("n", 0) == 0:
                continue
            print(
                f"  {b['range']:<12}  {b['n']:>5}  "
                f"{b['mean_pred']:>10.4f}  "
                f"{b['actual_rate']:>12.4f}  "
                f"{b['error']:>7.4f}"
            )
        ece_val = calib_1x2.get("ece", "—")
        print(f"\n  ECE (1X2): {ece_val}")

    print(f"\n{'=' * 60}\n")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Phase 5 -- Walk-forward Poisson+Elo backtest.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--league", type=int, default=None, metavar="ID",
                   help="provider_league_id (ej: 39 = Premier League).")
    p.add_argument("--season", type=int, default=None, metavar="YEAR",
                   help="Evaluar solo esta temporada como test.")
    p.add_argument("--train-seasons", type=int, default=3, metavar="N",
                   help="Numero de temporadas previas de entrenamiento (default: 3).")
    p.add_argument("--min-model-prob", type=float, default=0.0, metavar="P",
                   help="Probabilidad minima para incluir candidato (default: 0.0).")
    p.add_argument("--max-daily-picks", type=int, default=5, metavar="N",
                   help="Cap diario de picks seleccionados (default: 5).")
    p.add_argument("--rho", type=float, default=0.0, metavar="RHO",
                   help="Dixon-Coles rho correction (0.0=disabled, -0.13=typical).")
    p.add_argument("--elo-regress", type=float, default=0.0, metavar="F",
                   help="Seasonal Elo regression factor (0.0=disabled, 0.1=typical).")
    p.add_argument("--recent-weight", type=float, default=0.6, metavar="W",
                   help="Recency weight for most-recent training season (0.5=equal, 0.6=default, 1.0=only-recent).")
    p.add_argument("--min-seasons", type=int, default=2, metavar="N",
                   help="Minimo de temporadas totales en historia para procesar una liga (default: 2).")
    p.add_argument("--min-fixtures", type=int, default=10, metavar="N",
                   help="Minimo de fixtures completados en una temporada test (default: 10).")
    p.add_argument("--dry-run", action="store_true",
                   help="Calcula metricas sin escribir en DuckDB.")
    p.add_argument("--force", action="store_true",
                   help="Re-ejecutar aunque ya exista un run completado (evita dedup).")
    p.add_argument("--all-leagues", action="store_true",
                   help="Ejecutar backtest para todas las ligas en fixtures_history.")
    args = p.parse_args()

    if not args.league and not args.all_leagues:
        p.error("Especifica --league ID o --all-leagues.")

    setup_logger()
    conn = get_local_db()

    league_ids: list[int]
    if args.all_leagues:
        league_ids = history_repo.get_leagues_in_history(conn)
        if not league_ids:
            print("No hay ligas en fixtures_history. Ejecuta backfill_history_api.py primero.")
            sys.exit(2)
        seed_competition_context(conn, league_ids)
    else:
        league_ids = [args.league]

    # ── counters for final summary (only meaningful for --all-leagues) ─────────
    _summary: dict[str, list] = {
        "processed":                   [],
        "already_exists":              [],
        "skipped_no_data":             [],
        "skipped_insufficient_seasons": [],
        "skipped_no_test_data":        [],
        "error":                       [],
    }

    any_ok = False
    for league_id in league_ids:
        league_label = str(league_id)
        print(f"\nProcesando liga {league_id}...")
        try:
            result = run_backtest(
                league_id,
                dry_run=args.dry_run,
                force=args.force,
                only_season=args.season,
                train_seasons=args.train_seasons,
                min_model_prob=args.min_model_prob,
                max_daily_picks=args.max_daily_picks,
                rho=args.rho,
                elo_season_regress=args.elo_regress,
                recent_weight=args.recent_weight,
                min_seasons=args.min_seasons,
                min_fixtures_per_season=args.min_fixtures,
            )
        except Exception as exc:
            reason = str(exc)[:120]
            print(f"  ERROR: {reason}")
            _summary["error"].append({"league_id": league_id, "reason": reason})
            continue

        status = result.get("status", "ok")

        if status == "already_exists":
            name = result.get("league_name", league_label)
            print(f"  Ya completado ({result['run_name']}). Usa --force para re-ejecutar.")
            _summary["already_exists"].append(league_id)
            any_ok = True  # not an error; data already exists
            continue

        if status == "no_data":
            print(f"  Sin datos para liga {league_id}. Ejecuta backfill_history_api.py.")
            _summary["skipped_no_data"].append(league_id)
            continue

        if status == "season_not_found":
            print(
                f"  Temporada {result.get('season')} no encontrada en DuckDB "
                f"para liga {league_id}."
            )
            _summary["skipped_no_data"].append(league_id)
            continue

        if status == "insufficient_seasons":
            avail  = result.get("available", [])
            needed = result.get("needed", args.train_seasons + 1)
            print(
                f"  Temporadas insuficientes para liga {league_id}: "
                f"disponibles={avail}, necesarias={needed}."
            )
            _summary["skipped_insufficient_seasons"].append(league_id)
            continue

        if status == "no_test_data":
            print(
                f"  Liga {league_id}: sin fixtures suficientes en temporadas test "
                f"(min_fixtures={args.min_fixtures}). Elo actualizado igual."
            )
            _summary["skipped_no_test_data"].append(league_id)
            continue

        _print_report(result)
        _summary["processed"].append({
            "league_id":   league_id,
            "league_name": result.get("league_name", ""),
            "samples":     result.get("total_samples", 0),
            "run_id":      result.get("run_id"),
        })
        any_ok = True

    # ── Final summary (only printed for --all-leagues) ─────────────────────
    if args.all_leagues:
        conn2 = get_local_db()
        ts_count = conn2.execute("SELECT COUNT(*) FROM training_samples").fetchone()[0]
        bm_count = conn2.execute("SELECT COUNT(*) FROM backtest_metrics").fetchone()[0]

        print(f"\n{'=' * 60}")
        print(f"  RESUMEN MULTI-LIGA")
        print(f"  Total ligas detectadas       : {len(league_ids)}")
        print(f"  Procesadas (nuevas)          : {len(_summary['processed'])}")
        print(f"  Ya existentes (skip dedup)   : {len(_summary['already_exists'])}")
        print(f"  Sin datos suficientes        : {len(_summary['skipped_no_data'])}")
        print(f"  Temporadas insuficientes     : {len(_summary['skipped_insufficient_seasons'])}")
        print(f"  Sin test data                : {len(_summary['skipped_no_test_data'])}")
        print(f"  Errores                      : {len(_summary['error'])}")
        print(f"  training_samples total (DB)  : {ts_count}")
        print(f"  backtest_metrics total (DB)  : {bm_count}")

        if _summary["processed"]:
            print(f"\n  PROCESADAS:")
            for p in _summary["processed"]:
                print(f"    liga={p['league_id']:>4}  {p['league_name'][:28]:<28}  samples={p['samples']:>5}  run_id={p['run_id']}")

        if _summary["error"]:
            print(f"\n  ERRORES:")
            for e in _summary["error"]:
                print(f"    liga={e['league_id']:>4}  {e['reason']}")

        print(f"{'=' * 60}")

    if not any_ok:
        sys.exit(3)

    if args.dry_run:
        print("DRY-RUN completado. Nada fue escrito en DuckDB.")


if __name__ == "__main__":
    main()
