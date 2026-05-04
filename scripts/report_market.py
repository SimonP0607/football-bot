#!/usr/bin/env python
"""Phase 12: Market intelligence reports — CLV, signals, and closing line summaries.

Usage:
  python scripts/report_market.py --clv              # CLV performance summary
  python scripts/report_market.py --signals          # recent market movement signals
  python scripts/report_market.py --fixture 1035066  # full market summary for a fixture
  python scripts/report_market.py --clv --days 7     # last 7 days CLV
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_scripts_dir = Path(__file__).resolve().parent
_src_root = _scripts_dir.parent
sys.path.insert(0, str(_src_root))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Market intelligence reports")
    p.add_argument("--clv", action="store_true", help="Show CLV performance summary")
    p.add_argument("--signals", action="store_true", help="Show recent market movement signals")
    p.add_argument("--fixture", type=int, help="Full market summary for a fixture")
    p.add_argument("--days", type=int, default=30, help="Look-back period in days (default: 30)")
    p.add_argument("--league", type=int, help="Filter by league_id")
    p.add_argument("--market", help="Filter by market_key (e.g. 'Match Winner')")
    p.add_argument("-v", "--verbose", action="store_true", help="Show more detail")
    return p.parse_args()


def report_clv(conn, days: int, league_id: int | None, market_key: str | None) -> None:
    from app.data.local.market_intelligence_repo import get_clv_summary

    summary = get_clv_summary(conn, days=days, league_id=league_id, market_key=market_key)
    if not summary:
        print("No hay datos de CLV disponibles.")
        return

    total = summary.get("total", 0)
    beat = summary.get("beat_count", 0)
    avg_clv = summary.get("avg_clv")

    header = f"CLV Report — últimos {days} días"
    if league_id:
        header += f" | liga {league_id}"
    if market_key:
        header += f" | mercado '{market_key}'"
    print(f"\n{header}")
    print("─" * 50)
    print(f"  Picks con CLV       : {total}")
    if total == 0:
        print("  Sin datos de cierre disponibles aún.")
        return

    print(f"  Beat closing line   : {beat} ({beat/total*100:.1f}%)")
    if avg_clv is not None:
        sign = "+" if avg_clv >= 0 else ""
        print(f"  CLV promedio        : {sign}{avg_clv*100:.2f} pp")
    print(f"  Positivo            : {summary.get('positive_count', 0)}")
    print(f"  Neutro              : {summary.get('neutral_count', 0)}")
    print(f"  Negativo            : {summary.get('negative_count', 0)}")
    print(f"  Sin datos           : {summary.get('no_data_count', 0)}")

    if beat > 0 and total > 0:
        rate = beat / total
        if rate >= 0.55:
            assessment = "✓ Positivo — superamos la línea consistentemente."
        elif rate >= 0.45:
            assessment = "~ Neutro — en línea con el mercado."
        else:
            assessment = "✗ Por debajo de la línea — revisar selección de momios."
        print(f"\n  Evaluación          : {assessment}")


def report_signals(conn, days: int, market_key: str | None, verbose: bool) -> None:
    from datetime import datetime, timedelta, timezone
    from app.data.local.market_intelligence_repo import get_market_signals

    signals = get_market_signals(conn, market_key=market_key, limit=200)
    if not signals:
        print("\nNo hay señales de movimiento de mercado.")
        return

    # Filter by days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    signals = [s for s in signals if str(s.get("created_at", "")) >= cutoff]

    print(f"\nSeñales de mercado — últimos {days} días ({len(signals)} señales)")
    print("─" * 70)

    by_type: dict[str, list] = {}
    for sig in signals:
        stype = sig.get("signal_type", "unknown")
        by_type.setdefault(stype, []).append(sig)

    for stype, group in sorted(by_type.items(), key=lambda x: -len(x[1])):
        print(f"\n  [{stype.upper()}] — {len(group)} señales")
        limit = 10 if verbose else 3
        for sig in group[:limit]:
            pfid = sig.get("provider_fixture_id", "?")
            mk = sig.get("market_key", "")
            sel = sig.get("selection", "")
            delta = sig.get("implied_delta")
            delta_str = f"{delta*100:+.2f}pp" if delta is not None else "N/A"
            print(f"    fixture={pfid} | {mk} | {sel} | Δ={delta_str}")
            if verbose and sig.get("reason_text"):
                print(f"      → {sig['reason_text']}")


def report_fixture(conn, provider_fixture_id: int, verbose: bool) -> None:
    from app.services.market_intelligence_service import get_fixture_market_summary

    summary = get_fixture_market_summary(conn, provider_fixture_id)
    if not summary:
        print(f"\nNo hay datos de mercado para fixture {provider_fixture_id}.")
        return

    print(f"\nMarket Summary — fixture {provider_fixture_id}")
    print("─" * 60)

    closing_lines = summary.get("closing_lines", [])
    print(f"\n  Closing lines ({len(closing_lines)}):")
    for cl in closing_lines[:10]:
        mk = cl.get("market_key", "")
        sel = cl.get("selection", "")
        open_o = cl.get("opening_odds")
        close_o = cl.get("closing_odds")
        label = cl.get("movement_label", "no_data")
        open_str = f"{open_o:.2f}" if open_o else "N/A"
        close_str = f"{close_o:.2f}" if close_o else "N/A"
        print(f"    {mk:<25} {sel:<20} {open_str} → {close_str} [{label}]")

    signals = summary.get("signals", [])
    if signals:
        print(f"\n  Señales ({len(signals)}):")
        for sig in signals[:5]:
            print(
                f"    [{sig.get('signal_type', '?')}] {sig.get('market_key', '')} | "
                f"{sig.get('selection', '')} | Δ={sig.get('implied_delta', 0)*100:+.2f}pp"
            )

    flags = []
    if summary.get("has_steam"):
        flags.append("STEAM")
    if summary.get("has_reverse_line"):
        flags.append("REVERSE_LINE")
    if summary.get("has_sharp_move"):
        flags.append("SHARP_MOVE")
    if flags:
        print(f"\n  ⚠ Señales activas: {', '.join(flags)}")


def main() -> None:
    args = _parse_args()

    from app.data.local.duckdb_client import get_local_db, init_schema

    conn = get_local_db()
    init_schema(conn)

    shown = False

    if args.clv:
        report_clv(conn, args.days, args.league, args.market)
        shown = True

    if args.signals:
        report_signals(conn, args.days, args.market, args.verbose)
        shown = True

    if args.fixture:
        report_fixture(conn, args.fixture, args.verbose)
        shown = True

    if not shown:
        print("Uso: python scripts/report_market.py --clv | --signals | --fixture <id>")
        print("       Añade --help para ver todas las opciones.")


if __name__ == "__main__":
    main()
