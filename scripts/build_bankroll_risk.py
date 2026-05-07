#!/usr/bin/env python
"""Phase 14: Build bankroll risk recommendations.

Usage:
    python scripts/build_bankroll_risk.py --today
    python scripts/build_bankroll_risk.py --days 1 --dry-run
    python scripts/build_bankroll_risk.py --days 3 --execute
    python scripts/build_bankroll_risk.py --profile default --json
"""
import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.core.config import settings
    from app.core.logger import setup_logger
except ImportError as e:
    print(f"Error de importacion: {e}")
    sys.exit(1)

logger = logging.getLogger(__name__)


def main(args: argparse.Namespace) -> None:
    days     = 1 if args.today else args.days
    dry_run  = not args.execute
    profile  = args.profile or "default"
    as_json  = args.json

    if not as_json:
        print(f"\n=== build_bankroll_risk ===")
        print(f"Dias: {days}  |  Dry-run: {dry_run}  |  Perfil: {profile}")

    from app.data.local.duckdb_client import get_local_db, init_schema
    conn = get_local_db()
    init_schema(conn)

    from app.services.bankroll_risk_service import run_bankroll_risk
    result = run_bankroll_risk(conn, days=days, dry_run=dry_run, profile_name=profile)

    if as_json:
        print(json.dumps(result, indent=2, default=str))
        return

    print(f"\nResultado:")
    print(f"  Picks analizados:  {result.get('picks', 0)}")
    print(f"  Con stake:         {result.get('with_stake', 0)}")
    print(f"  Rechazados:        {result.get('rejected', 0)}")
    print(f"  Total unidades:    {result.get('total_units', 0.0):.2f}u")
    print(f"  Portfolio score:   {result.get('portfolio_score', 0):.1f}/100")
    print(f"  Nivel de riesgo:   {result.get('risk_level', 'unknown').upper()}")
    print(f"  Tiempo:            {result.get('elapsed_s', 0):.2f}s")

    warnings = result.get("warnings") or []
    if warnings:
        print(f"\n  Alertas ({len(warnings)}):")
        for w in warnings:
            print(f"    - {w}")

    if dry_run:
        print("\n[DRY RUN] — no se guardaron cambios en la base de datos.")
        print("Usa --execute para persistir las recomendaciones.")
    else:
        print("\nRecomendaciones guardadas en stake_recommendations y portfolio_risk_snapshots.")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build bankroll risk recommendations.")
    p.add_argument("--days", type=int, default=1, help="Ventana de picks en dias (default: 1)")
    p.add_argument("--today", action="store_true", help="Equivalente a --days 1")
    p.add_argument("--execute", action="store_true", help="Persistir recomendaciones (default: dry-run)")
    p.add_argument("--dry-run", action="store_true", default=True, help="Solo calcular, no guardar (default)")
    p.add_argument("--profile", default="default", help="Nombre del perfil de bankroll (default: 'default')")
    p.add_argument("--json", action="store_true", help="Salida en formato JSON")
    return p.parse_args()


if __name__ == "__main__":
    setup_logger()
    args = parse_args()
    main(args)
