#!/usr/bin/env python
"""Shadow mode: predict picks for an upcoming fixture (no production writes).

Queries current Elo state and recent goal stats from local DuckDB to generate
model probabilities for a match that has NOT yet been played.

No Supabase, no Telegram, no modification of any production system.

To get team IDs, query DuckDB:
    SELECT DISTINCT home_team_id, home_team_name
    FROM fixtures_history
    WHERE provider_league_id = 39
    ORDER BY home_team_name;

Usage:
    python scripts/shadow_picks.py --league 39 --home 33 --away 40
    python scripts/shadow_picks.py --league 39 --home 33 --away 40 --rho -0.13
    python scripts/shadow_picks.py --league 39 --home 33 --away 40 --date 2025-08-20

Exit codes:
    0  prediction produced
    2  no data for league
    1  import error
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.core.logger import setup_logger
    from app.services.shadow_service import predict_fixture
    from app.services.backtest_service import MARKETS
except ImportError as e:
    print(f"Error de importacion: {e}")
    sys.exit(1)


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _print_prediction(result: dict) -> None:
    if "error" in result:
        print(f"\n  ERROR: {result['message']}")
        return

    print(f"\n{'=' * 56}")
    print(f"  SHADOW PICK (sin publicacion)")
    print(f"  Liga    : {result['league_id']} ({result['league_name']})")
    print(f"  Scope   : {result['entity_scope']}")
    print(f"  Neutral : {result['is_neutral']}")
    print(f"  Fecha   : {result['fixture_date']}")
    print(f"  Home ID : {result['home_team_id']}  Elo={result['home_elo']:.0f}")
    print(f"  Away ID : {result['away_team_id']}  Elo={result['away_elo']:.0f}")
    print(f"  Elo diff: {result['elo_diff']:+.0f}  (+ = home ventaja)")
    print(f"  Lambda  : home={result['lambda_home']:.3f}  away={result['lambda_away']:.3f}")
    print(f"  Train   : {result['train_window']}  rho={result['rho']}")
    print(f"  Elo DB  : {result['elo_teams_loaded']} equipos cargados")
    print(f"{'=' * 56}")

    picks = result.get("picks", {})
    if picks:
        print(f"\n  PROBABILIDADES Y PICKS RECOMENDADOS")
        print(f"  {'Mercado':<6}  {'Seleccion':<14}  {'Prob':>6}  {'Todas las opciones'}")
        print(f"  {'-' * 62}")
        for market in MARKETS:
            pk = picks.get(market)
            if not pk:
                continue
            sel  = pk["selection"]
            prob = _pct(pk["probability"])
            all_p = pk["all_probs"]
            options = "  ".join(
                f"{k}={_pct(v)}" for k, v in sorted(all_p.items(), key=lambda x: -x[1])
            )
            print(f"  {market:<6}  {sel:<14}  {prob:>6}  {options}")

    print(f"\n  NOTA: implied_probability/edge/ev_value = None (Phase 5 -- sin odds)")
    print(f"  Proximo paso: backfillear odds historicas y conectar Phase 5.5")
    print(f"{'=' * 56}\n")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Shadow mode: predice picks para un partido proximo.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--league",    type=int, required=True,  metavar="ID",
                   help="provider_league_id (ej: 39 = Premier League).")
    p.add_argument("--home",      type=int, required=True,  metavar="ID",
                   help="provider_team_id del equipo local.")
    p.add_argument("--away",      type=int, required=True,  metavar="ID",
                   help="provider_team_id del equipo visitante.")
    p.add_argument("--date",      type=str, default=None,   metavar="YYYY-MM-DD",
                   help="Fecha del partido (solo informativo).")
    p.add_argument("--rho",       type=float, default=0.0,  metavar="RHO",
                   help="Dixon-Coles rho (0.0=off, -0.13=tipico).")
    p.add_argument("--train-seasons", type=int, default=2,  metavar="N",
                   help="Temporadas recientes para estadisticas de goles (default: 2).")
    p.add_argument("--recent-weight", type=float, default=0.6, metavar="W",
                   help="Peso de la temporada mas reciente en stats (default: 0.6).")
    args = p.parse_args()

    setup_logger()
    logging.getLogger("app.services").setLevel(logging.WARNING)
    logging.getLogger("app.data").setLevel(logging.WARNING)

    result = predict_fixture(
        league_id=args.league,
        home_team_id=args.home,
        away_team_id=args.away,
        fixture_date=args.date,
        rho=args.rho,
        train_seasons=args.train_seasons,
        recent_weight=args.recent_weight,
    )

    _print_prediction(result)

    if "error" in result:
        sys.exit(2)


if __name__ == "__main__":
    main()
