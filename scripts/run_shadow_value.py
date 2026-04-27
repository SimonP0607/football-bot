#!/usr/bin/env python
"""Shadow value engine: calibrated pick evaluation for an upcoming fixture.

Combines shadow mode prediction (Elo + Poisson) with probability calibration
to produce fair_odds, w_rel, risk_score, and quality_score per market.

No production writes (Supabase/Telegram untouched).
Results are persisted to shadow_value_picks in DuckDB for audit.

Prerequisites:
    1. python scripts/run_backtest.py --league 39
       (populates training_samples + team_elo_history)
    2. python scripts/run_calibration.py --league 39
       (populates calibration_registry)

Usage:
    python scripts/run_shadow_value.py --league 39 --home 42 --away 66
    python scripts/run_shadow_value.py --league 39 --home 42 --away 66 --date 2025-08-20
    python scripts/run_shadow_value.py --league 39 --home 42 --away 66 --rho -0.13
    python scripts/run_shadow_value.py --league 39 --home 42 --away 66 --no-persist

Exit codes:
    0  pick evaluated (or no pick selected — still a valid outcome)
    2  no data for league
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.core.logger import setup_logger
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.data.local.history_repo import get_competition_context
    from app.services.shadow_service import predict_fixture
    from app.services.value_betting_service import evaluate_fixture, MIN_PCAL, MIN_WREL
    from app.services.backtest_service import MARKETS
except ImportError as e:
    print(f"Error de importacion: {e}")
    sys.exit(1)


def _pct(v: float | None) -> str:
    if v is None:
        return "  -- "
    return f"{v * 100:.1f}%"


def _odds(v: float | None) -> str:
    if v is None:
        return "  -- "
    return f"{v:.2f}"


def _decision_tag(status: str | None) -> str:
    if status == "selected":
        return "SELECCIONADO"
    if status == "rejected_cap":
        return "rechazado_cap"
    return "rechazado_filtro"


def _print_report(shadow: dict, value: dict, persist: bool) -> None:
    selected   = value.get("selected")
    candidates = value.get("candidates", [])

    print(f"\n{'=' * 68}")
    print(f"  SHADOW VALUE ENGINE  (Phase 5 -- sin odds)")
    print(f"  Liga    : {shadow['league_id']} ({shadow['league_name']})")
    print(f"  Scope   : {shadow['entity_scope']}  Neutral={shadow['is_neutral']}")
    print(f"  Fecha   : {shadow['fixture_date']}")
    print(f"  Home    : {shadow['home_team_id']}  Elo={shadow['home_elo']:.0f}")
    print(f"  Away    : {shadow['away_team_id']}  Elo={shadow['away_elo']:.0f}")
    print(f"  Elo diff: {shadow['elo_diff']:+.0f}  (+= home ventaja)")
    print(f"  Lambda  : home={shadow['lambda_home']:.3f}  away={shadow['lambda_away']:.3f}")
    print(f"  Elo DB  : {shadow['elo_teams_loaded']} equipos cargados")
    print(f"{'=' * 68}")

    any_uncalibrated = any(not c.get("_calibrated") for c in candidates)

    print(f"\n  EVALUACION POR MERCADO  (filtros: p_cal>={_pct(MIN_PCAL)}  w_rel>={MIN_WREL:.2f})")
    header = (
        f"  {'Mkt':<5}  {'Seleccion':<14}  "
        f"{'p_raw':>6}  {'p_cal':>6}  {'fair_odds':>9}  "
        f"{'w_rel':>6}  {'quality':>7}  decision"
    )
    print(header)
    print(f"  {'-' * (len(header) - 2)}")

    for mkt in MARKETS:
        c = next((x for x in candidates if x["market_key"] == mkt), None)
        if not c:
            continue
        calib_tag = "" if c.get("_calibrated") else "*"
        print(
            f"  {c['market_key']:<5}  {c['selection']:<14}  "
            f"{_pct(c['p_raw']):>6}  {_pct(c['p_cal']):>6}  "
            f"{_odds(c['fair_odds']):>9}  "
            f"{c['w_rel']:>6.3f}  {c['quality_score']:>7.4f}"
            f"  {_decision_tag(c.get('decision_status'))}{calib_tag}"
        )

    print()
    if selected:
        meta = selected.get("_meta") or {}
        calib_scope = "sin calibrador"
        if selected.get("_calibrated") and meta:
            calib_scope = (
                f"calibrador n={meta['n_train']}  "
                f"ECE {meta['ece_before']:.4f}->{meta['ece_after']:.4f}"
            )
        print(f"  PICK SELECCIONADO:")
        print(f"    Mercado    : {selected['market_key']}")
        print(f"    Seleccion  : {selected['selection']}")
        print(f"    p_raw      : {_pct(selected['p_raw'])}")
        print(f"    p_cal      : {_pct(selected['p_cal'])}")
        print(f"    fair_odds  : {_odds(selected['fair_odds'])}")
        print(f"    w_rel      : {selected['w_rel']:.3f}")
        print(f"    quality    : {selected['quality_score']:.4f}")
        print(f"    Calibrador : {calib_scope}")
    else:
        print(f"  PICK SELECCIONADO: ninguno (sin candidatos que pasen los filtros)")
        print(f"  Ajusta --rho o verifica que run_calibration.py fue ejecutado.")

    if any_uncalibrated:
        print(f"\n  * Mercados sin calibrador: p_cal = p_raw (ejecuta run_calibration.py)")

    print(f"\n  NOTA: edge / EV / ev_adj = NULL (Phase 5 -- sin odds historicas)")
    print(f"  Proximo paso: backfillear odds (Phase 5.5) para activar el motor de value completo")

    if persist and candidates:
        print(f"\n  {len(candidates)} picks guardados en shadow_value_picks.")

    print(f"{'=' * 68}\n")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Shadow value engine: predice y calibra picks para un partido proximo.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--league",  type=int, required=True,  metavar="ID",
                   help="provider_league_id (ej: 39 = Premier League).")
    p.add_argument("--home",    type=int, required=True,  metavar="ID",
                   help="provider_team_id del equipo local.")
    p.add_argument("--away",    type=int, required=True,  metavar="ID",
                   help="provider_team_id del equipo visitante.")
    p.add_argument("--date",    type=str, default=None,   metavar="YYYY-MM-DD",
                   help="Fecha del partido (informativo y clave de persistencia).")
    p.add_argument("--rho",     type=float, default=0.0,  metavar="RHO",
                   help="Dixon-Coles rho (0.0=off, -0.13=tipico).")
    p.add_argument("--train-seasons", type=int, default=2, metavar="N",
                   help="Temporadas para estadisticas de goles (default: 2).")
    p.add_argument("--recent-weight", type=float, default=0.6, metavar="W",
                   help="Peso de temporada reciente en stats (default: 0.6).")
    p.add_argument("--no-persist", action="store_true",
                   help="No escribir en shadow_value_picks.")
    args = p.parse_args()

    setup_logger()
    logging.getLogger("app.services").setLevel(logging.WARNING)
    logging.getLogger("app.data").setLevel(logging.WARNING)

    conn = get_local_db()
    init_schema(conn)

    # Step 1: raw Poisson + Elo prediction
    shadow = predict_fixture(
        conn,
        league_id=args.league,
        home_team_id=args.home,
        away_team_id=args.away,
        fixture_date=args.date,
        rho=args.rho,
        train_seasons=args.train_seasons,
        recent_weight=args.recent_weight,
    )

    if "error" in shadow:
        print(f"\n  ERROR: {shadow['message']}")
        sys.exit(2)

    # Step 2: competition context for entity_type
    ctx              = get_competition_context(conn, args.league)
    entity_type      = ctx["entity_scope"]    if ctx else "club"
    competition_type = ctx["competition_type"] if ctx else "domestic_league"

    # Step 3: calibrate + score
    value = evaluate_fixture(
        conn,
        fixture_picks=shadow["picks"],
        provider_league_id=args.league,
        entity_type=entity_type,
        competition_type=competition_type,
        run_date=args.date,
        persist=not args.no_persist,
    )

    _print_report(shadow, value, persist=not args.no_persist)


if __name__ == "__main__":
    main()
