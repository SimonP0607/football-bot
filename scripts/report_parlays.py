"""CLI: Report on parlay engine results.

Usage:
  python scripts/report_parlays.py --today
  python scripts/report_parlays.py --days 7
  python scripts/report_parlays.py --days 30
  python scripts/report_parlays.py --recommended
  python scripts/report_parlays.py --settled
  python scripts/report_parlays.py --pending
  python scripts/report_parlays.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

_STATUS_ICON = {"win": "✅", "loss": "❌", "void": "⬜", "pending": "⏳"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Report parlay engine results")
    parser.add_argument("--today", action="store_true",
                        help="Show today's parlays")
    parser.add_argument("--days", type=int, default=7,
                        help="Look back N days for settled results (default: 7)")
    parser.add_argument("--recommended", action="store_true",
                        help="Only show recommended parlays")
    parser.add_argument("--settled", action="store_true",
                        help="Only show settled parlays")
    parser.add_argument("--pending", action="store_true",
                        help="Only show pending parlays")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="Output as JSON")
    parser.add_argument("--limit", type=int, default=10,
                        help="Max rows to display (default: 10)")
    args = parser.parse_args()

    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.data.local import parlay_repo as repo

    conn = get_local_db()
    init_schema(conn)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if args.as_json:
        _output_json(conn, repo, today, args)
        return

    # Header
    print(f"\n{'='*60}")
    print(f"  Parlay Engine Report")
    print(f"  Date: {today}")
    print(f"{'='*60}\n")

    # Summary
    summary = repo.get_recent_parlay_summary(conn, days=args.days)
    print(f"Performance (últimos {args.days} días):")
    print(f"  Settled: {summary['settled']}  "
          f"Wins: {summary['wins']}  "
          f"Losses: {summary['losses']}  "
          f"Voids: {summary['voids']}")
    if summary["settled"] > 0:
        print(f"  Profit: {summary['profit_units']:+.4f}u  "
              f"ROI: {summary['roi_pct']:+.2f}%")
    print()

    # Today's candidates
    status_filter = "recommended" if args.recommended else None
    date_filter   = today if args.today else None

    candidates = repo.get_parlay_candidates(conn, date=date_filter, status=status_filter, limit=args.limit)

    if args.settled:
        results = repo.get_parlay_results(conn, days=args.days)
        _print_settled(results, args.limit)
    elif args.pending:
        pending = [c for c in candidates if not _has_result(conn, repo, c["parlay_key"])]
        _print_candidates(pending, args.limit, show_legs=False)
    else:
        _print_candidates(candidates, args.limit, show_legs=True)
        if not args.today:
            print()
            results = repo.get_parlay_results(conn, days=args.days)
            _print_settled(results, 5)


def _has_result(conn, repo, parlay_key: str) -> bool:
    row = conn.execute(
        "SELECT result_status FROM parlay_results WHERE parlay_key = ?", [parlay_key]
    ).fetchone()
    return row is not None and row[0] != "pending"


def _print_candidates(candidates: list[dict], limit: int, show_legs: bool) -> None:
    if not candidates:
        print("Sin parlays encontrados.")
        return

    print(f"Parlays candidatos ({len(candidates)}):")
    sep = "─" * 55
    for c in candidates[:limit]:
        print(sep)
        status = c.get("recommendation_status", "?")
        ptype  = c.get("parlay_type", "?")
        odds   = c.get("total_odds") or 0
        jp     = (c.get("joint_probability") or 0) * 100
        ev     = (c.get("ev") or 0) * 100
        edge   = (c.get("edge") or 0) * 100
        risk   = (c.get("risk_score") or 0) * 100
        conf   = (c.get("confidence_score") or 0) * 100
        date   = c.get("date", "?")
        print(f"  [{status}]  {ptype}  {date}  {c['parlay_key'][:32]}...")
        print(f"  Cuota: {odds:.2f}x  Prob: {jp:.1f}%  EV: {ev:+.1f}%  "
              f"Edge: {edge:+.1f}%  Riesgo: {risk:.0f}%  Conf: {conf:.0f}%")


def _print_settled(results: list[dict], limit: int) -> None:
    if not results:
        print("Sin parlays liquidados.")
        return

    print(f"Resultados liquidados ({len(results)}):")
    sep = "─" * 55
    for r in results[:limit]:
        print(sep)
        icon   = _STATUS_ICON.get(r["result_status"], "?")
        profit = r.get("profit_units") or 0
        roi    = r.get("roi") or 0
        odds   = r.get("total_odds") or 0
        won    = r.get("legs_won", 0)
        lost   = r.get("legs_lost", 0)
        void_c = r.get("legs_void", 0)
        print(f"  {icon} {r['result_status'].upper()}  {r['parlay_type']}  {r['date']}")
        print(f"  Cuota: {odds:.2f}x  Legs: {won}W/{lost}L/{void_c}V  "
              f"Profit: {profit:+.4f}u  ROI: {roi:+.2f}%")


def _output_json(conn, repo, today: str, args) -> None:
    data = {
        "date": today,
        "summary": repo.get_recent_parlay_summary(conn, days=args.days),
        "candidates": repo.get_parlay_candidates(conn, date=today if args.today else None, limit=args.limit),
        "results": repo.get_parlay_results(conn, days=args.days),
    }
    print(json.dumps(data, indent=2, default=str))


if __name__ == "__main__":
    main()
