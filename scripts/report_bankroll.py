#!/usr/bin/env python
"""Phase 14: Bankroll risk report.

Usage:
    python scripts/report_bankroll.py --today
    python scripts/report_bankroll.py --days 7
    python scripts/report_bankroll.py --risk
    python scripts/report_bankroll.py --stakes
    python scripts/report_bankroll.py --portfolio
    python scripts/report_bankroll.py --json
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


def _u(v, d=2):
    return f"{v:.{d}f}u" if v is not None else "n/a"


def _pct(v):
    return f"{v:+.1f}%" if v is not None else "n/a"


def main(args: argparse.Namespace) -> None:
    days    = 1 if args.today else args.days
    as_json = args.json

    try:
        from app.data.local.duckdb_client import get_local_db, init_schema
        conn = get_local_db()
        init_schema(conn)
    except Exception as exc:
        print(f"ERROR: DuckDB no disponible — {exc}")
        sys.exit(1)

    output: dict = {"days": days, "sections": {}}

    # ── Portfolio snapshot ─────────────────────────────────────────────────────
    if args.portfolio or args.today or not any([args.risk, args.stakes]):
        try:
            from app.data.local.bankroll_risk_repo import get_latest_portfolio_snapshot
            snap = get_latest_portfolio_snapshot(conn)
            if snap:
                output["sections"]["portfolio"] = {
                    "snapshot_date": snap.get("snapshot_date"),
                    "portfolio_score": snap.get("portfolio_score"),
                    "risk_level": snap.get("risk_level"),
                    "total_picks": snap.get("total_picks"),
                    "total_units": snap.get("total_recommended_units"),
                    "warnings": snap.get("warnings_json") or [],
                }
        except Exception as exc:
            logger.warning("portfolio snapshot: %s", exc)

    # ── Risk events ────────────────────────────────────────────────────────────
    if args.risk or args.today or not any([args.portfolio, args.stakes]):
        try:
            from app.data.local.bankroll_risk_repo import get_risk_events
            events = get_risk_events(conn, days=days)
            output["sections"]["risk_events"] = [
                {k: v for k, v in e.items() if k != "created_at"}
                for e in events[:20]
            ]
        except Exception as exc:
            logger.warning("risk events: %s", exc)

    # ── Stakes ────────────────────────────────────────────────────────────────
    if args.stakes or args.today or not any([args.risk, args.portfolio]):
        try:
            from app.data.local.bankroll_risk_repo import (
                get_stake_recommendations_by_day, get_bankroll_summary,
            )
            recs    = get_stake_recommendations_by_day(conn, days=days)
            summary = get_bankroll_summary(conn, days=days)
            output["sections"]["stakes"] = {
                "summary": summary,
                "top_recs": [
                    {
                        "pick_candidate_id": r.get("pick_candidate_id"),
                        "market_key":        r.get("market_key"),
                        "selection":         r.get("selection"),
                        "league_id":         r.get("league_id"),
                        "recommended_units": r.get("recommended_units"),
                        "stake_label":       r.get("stake_label"),
                        "risk_score":        r.get("risk_score"),
                        "rejection_reason":  r.get("rejection_reason"),
                    }
                    for r in recs[:20]
                ],
            }
        except Exception as exc:
            logger.warning("stakes: %s", exc)

    if as_json:
        print(json.dumps(output, indent=2, default=str))
        return

    # ── Text output ────────────────────────────────────────────────────────────
    print(f"\n=== Bankroll Risk Report (ultimos {days}d) ===")

    portfolio = output["sections"].get("portfolio")
    if portfolio:
        print("\n--- Portfolio ---")
        print(f"  Fecha:    {portfolio.get('snapshot_date', 'n/a')}")
        score = portfolio.get("portfolio_score")
        print(f"  Score:    {f'{score:.1f}/100' if score else 'n/a'}")
        print(f"  Nivel:    {(portfolio.get('risk_level') or 'unknown').upper()}")
        print(f"  Picks:    {portfolio.get('total_picks', 0)}")
        print(f"  Unidades: {_u(portfolio.get('total_units'))}")
        warnings = portfolio.get("warnings") or []
        if warnings:
            print(f"  Alertas ({len(warnings)}):")
            for w in warnings:
                print(f"    - {w}")

    stakes_section = output["sections"].get("stakes")
    if stakes_section:
        print("\n--- Stakes ---")
        s = stakes_section.get("summary") or {}
        print(f"  Recomendaciones: {s.get('total_recommendations', 0)}")
        print(f"  Con stake:       {s.get('with_stake', 0)}")
        print(f"  Rechazados:      {s.get('rejected', 0)}")
        avg = s.get("avg_recommended_units")
        if avg:
            print(f"  Avg unidades:    {avg:.2f}u")
        top = stakes_section.get("top_recs") or []
        active = [r for r in top if (r.get("recommended_units") or 0) > 0]
        if active:
            print(f"\n  Top stakes:")
            for r in active[:5]:
                print(f"    [{r.get('stake_label', '?'):6s}] "
                      f"{r.get('market_key', '?')}/{r.get('selection', '?'):15s} "
                      f"  {_u(r.get('recommended_units'))}  riesgo={r.get('risk_score', 0):.0f}")

    events = output["sections"].get("risk_events") or []
    if events:
        print(f"\n--- Eventos de riesgo ({len(events)}) ---")
        for ev in events[:5]:
            sev   = ev.get("severity", "?")
            etype = ev.get("event_type", "?")
            msg   = ev.get("message", "")[:80]
            print(f"  [{sev.upper():6s}] {etype}: {msg}")

    print()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Bankroll risk report.")
    p.add_argument("--days", type=int, default=7, help="Ventana en dias (default: 7)")
    p.add_argument("--today", action="store_true", help="Equivalente a --days 1")
    p.add_argument("--risk", action="store_true", help="Solo eventos de riesgo")
    p.add_argument("--stakes", action="store_true", help="Solo stake recommendations")
    p.add_argument("--portfolio", action="store_true", help="Solo portfolio snapshot")
    p.add_argument("--json", action="store_true", help="Salida en formato JSON")
    return p.parse_args()


if __name__ == "__main__":
    setup_logger()
    args = parse_args()
    main(args)
