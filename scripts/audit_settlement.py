#!/usr/bin/env python
"""Read-only diagnostic for the settlement pipeline.

Shows:
  - Pending picks with fixture status and DuckDB availability
  - Settled picks summary (win/loss/void/ROI)
  - Missing results (fixtures past kickoff with no score)

Usage:
    python scripts/audit_settlement.py
    python scripts/audit_settlement.py --days 7
    python scripts/audit_settlement.py --pending-only
    python scripts/audit_settlement.py --settled-only
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: F401 -- loads .env
from app.data.repositories import settlement_repo

_FINISHED = {"FT", "AET", "PEN", "WO"}
_MIN_WAIT = timedelta(hours=2, minutes=30)


def _check_duckdb(provider_fixture_id: int) -> str:
    """Return 'FT', status_short string, 'no_data', or 'error'."""
    try:
        import duckdb
        db = duckdb.connect(settings.local_db_path, read_only=True)
        row = db.execute(
            "SELECT goals_home, goals_away, status_short FROM fixtures_history WHERE id = ?",
            [provider_fixture_id],
        ).fetchone()
        db.close()
        if row is None:
            return "no_data"
        gh, ga, st = row
        if gh is not None and ga is not None:
            return f"{st} {gh}-{ga}"
        return f"{st} (sin goles)"
    except Exception as exc:
        return f"error:{exc}"


def _market_label(market: str) -> str:
    return {"1X2": "1X2", "OU25": "O/U2.5", "BTTS": "BTTS"}.get(market, market)


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnostico del pipeline de settlement")
    parser.add_argument("--days", type=int, default=None,
                        help="Limitar a los ultimos N dias de kickoffs")
    parser.add_argument("--pending-only", action="store_true",
                        help="Solo mostrar picks pendientes")
    parser.add_argument("--settled-only", action="store_true",
                        help="Solo mostrar picks resueltos")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)

    print()
    print("=" * 68)
    print("  AUDIT: Settlement Pipeline")
    print(f"  {now.strftime('%Y-%m-%d %H:%M')} UTC")
    print("=" * 68)

    # -- Pending picks ----------------------------------------------------------
    if not args.settled_only:
        pending = settlement_repo.get_all_pending_with_fixtures()
        if args.days:
            cutoff = (now - timedelta(days=args.days)).isoformat()
            pending = [
                p for p in pending
                if (p.get("_fixture", {}).get("kickoff_at") or "9999") >= cutoff
            ]

        print(f"\n  PENDIENTES ({len(pending)}):")
        if not pending:
            print("    Ninguno.")
        else:
            print(f"  {'ID':>4}  {'Fixture':>10}  {'Kickoff':>16}  {'Mercado':>8}  {'Sel':>15}  {'Odd':>5}  {'DuckDB'}  {'Estado'}")
            print("  " + "-" * 90)
            ready_count = 0
            for p in sorted(pending, key=lambda x: x.get("_fixture", {}).get("kickoff_at") or ""):
                fix = p.get("_fixture", {})
                pfid = fix.get("provider_fixture_id", "?")
                ko = (fix.get("kickoff_at") or "")[:16].replace("T", " ")
                mk = _market_label(p.get("market_key", "?"))
                sel = p.get("selection", "?")[:15]
                odd = f"{float(p.get('odd_taken') or 0):.2f}"
                ddb = _check_duckdb(pfid) if pfid != "?" else "--"

                try:
                    ko_dt = datetime.fromisoformat(
                        fix.get("kickoff_at", "").replace("Z", "+00:00")
                    )
                    if ko_dt + _MIN_WAIT <= now:
                        estado = "LISTO" if "FT" in ddb or any(s in ddb for s in _FINISHED) else "pendiente-API"
                        ready_count += 1
                    else:
                        estado = "en-juego"
                except ValueError:
                    estado = "?"

                print(f"  {p['id']:>4}  {pfid!s:>10}  {ko:>16}  {mk:>8}  {sel:>15}  {odd:>5}  {ddb:<20}  {estado}")

        if not args.settled_only and pending:
            ready = sum(
                1 for p in pending
                if (p.get("_fixture", {}).get("kickoff_at") or "9999") <= (now - _MIN_WAIT).isoformat()
            )
            print(f"\n  Listos para settle_results.py: {ready} de {len(pending)}")
            if ready > 0:
                print("  Ejecuta: python scripts/settle_results.py --dry-run")

    # -- Settled picks summary --------------------------------------------------
    if not args.pending_only:
        settled = settlement_repo.get_settled(days=args.days)
        stats = settlement_repo.compute_performance_stats(settled)
        by_mkt = settlement_repo.get_stats_by_market(settled)

        print(f"\n  RESUELTOS ({stats['total']}):")

        if stats["total"] == 0:
            print("    Sin picks resueltos todavia.")
            print("    Ejecuta: python scripts/settle_results.py")
        else:
            period_label = f"ultimos {args.days}d" if args.days else "historico"
            print(f"  Periodo: {period_label}")
            print()
            print(f"    Ganados:   {stats['wins']:>4}")
            print(f"    Perdidos:  {stats['losses']:>4}")
            print(f"    Anulados:  {stats['voids']:>4}")
            print(f"    Resueltos: {stats['settled']:>4}  (excluye anulados)")
            if stats["settled"]:
                print(f"    Hit rate:  {stats['hit_rate_pct']:>5.1f}%")
                print(f"    Profit:    {stats['profit_units']:>+7.4f} u")
                print(f"    ROI:       {stats['roi_pct']:>+6.2f}%")

            if stats["settled"] < 20:
                print()
                print("    !  Muestra pequena (< 20 picks resueltos).")
                print("       Las metricas no son estadisticamente significativas.")

            if by_mkt:
                print()
                print(f"  {'Mercado':>8}  {'Picks':>5}  {'W':>4}  {'L':>4}  {'%Acierto':>9}  {'Profit':>8}  {'ROI':>7}")
                print("  " + "-" * 56)
                for mk, s in by_mkt.items():
                    if s["settled"]:
                        print(
                            f"  {mk:>8}  {s['settled']:>5}  {s['wins']:>4}  {s['losses']:>4}"
                            f"  {s['hit_rate_pct']:>8.1f}%  {s['profit_units']:>+7.4f}u  {s['roi_pct']:>+6.2f}%"
                        )

    print()
    print("=" * 68)
    print()


if __name__ == "__main__":
    main()
