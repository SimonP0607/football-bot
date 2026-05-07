#!/usr/bin/env python
"""Phase 13: Report on strategy profiles — best, worst, or a specific key.

Usage:
    python scripts/report_strategies.py --best --days 30
    python scripts/report_strategies.py --worst --days 30
    python scripts/report_strategies.py --strategy-key "OU25|league_39|..."
    python scripts/report_strategies.py --market OU25 --days 60
    python scripts/report_strategies.py --best --json
"""
import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logger import setup_logger

logger = logging.getLogger(__name__)


def _fmt_pct(v):
    if v is None:
        return "  n/a"
    return f"{v * 100:+.1f}%"


def _fmt_clv(v):
    if v is None:
        return "  n/a"
    return f"{v:+.2f}"


def _print_profile(p: dict, verbose: bool = False) -> None:
    key   = p.get("strategy_key") or "-"
    mkt   = p.get("market_key") or "-"
    lg    = p.get("league_name") or (f"liga {p['league_id']}" if p.get("league_id") else "global")
    n     = p.get("sample_size") or 0
    wins  = p.get("wins") or 0
    loss  = p.get("losses") or 0
    voids = p.get("voids") or 0
    hr    = p.get("hit_rate")
    roi   = p.get("roi")
    clv   = p.get("avg_clv_percent")
    beat  = p.get("clv_beat_rate")
    score = p.get("strategy_score") or 0
    rec   = p.get("recommendation") or "-"
    stab  = p.get("stability_score")

    print(f"  Strategy:    {key[:70]}")
    print(f"  Market:      {mkt}   League: {lg}")
    print(f"  Sample:      {n} picks  ({wins}W / {loss}L / {voids}V)")
    print(f"  Hit rate:    {hr * 100:.1f}%" if hr is not None else "  Hit rate:    n/a")
    print(f"  ROI:         {_fmt_pct(roi)}")
    print(f"  Avg CLV:     {_fmt_clv(clv)}")
    print(f"  CLV beat:    {beat * 100:.1f}%" if beat is not None else "  CLV beat:    n/a")
    print(f"  Score:       {score:.1f} / 100")
    print(f"  Stability:   {stab:.2f}" if stab is not None else "  Stability:   n/a")
    print(f"  Recommend:   {rec.upper()}")
    if verbose:
        print(f"  Buckets:     {p.get('odds_bucket')} | {p.get('edge_bucket')} | "
              f"{p.get('confidence_bucket')} | {p.get('clv_bucket')}")
    print()


def main(args: argparse.Namespace) -> None:
    from app.data.local.duckdb_client import get_local_db, init_schema
    conn = get_local_db()
    init_schema(conn)

    from app.data.local.strategy_learning_repo import (
        get_best_strategies, get_weak_strategies,
        get_strategy_by_key, get_strategy_profiles,
    )

    if args.strategy_key:
        p = get_strategy_by_key(conn, args.strategy_key)
        if not p:
            print(f"Strategy key not found: {args.strategy_key}")
            return
        if args.json:
            print(json.dumps(p, indent=2, default=str))
            return
        print(f"\n=== Strategy Detail ===\n")
        _print_profile(p, verbose=True)

        # Recent annotations for this key
        try:
            rows = conn.execute(
                """
                SELECT learning_label, result_status, clv_percent, beat_closing_line
                FROM pick_learning_annotations
                WHERE strategy_key = ?
                ORDER BY created_at DESC LIMIT 10
                """,
                [args.strategy_key],
            ).fetchall()
            if rows:
                print("  Recent picks:")
                for lbl, rs, clv, beat in rows:
                    clv_s = f"{clv:+.2f}" if clv is not None else "n/a"
                    beat_s = "beat" if beat else "miss"
                    print(f"    {lbl:18s} {rs:6s}  clv={clv_s:7s}  {beat_s}")
        except Exception:
            pass
        return

    if args.best:
        print(f"\n=== Best Strategies (top 10) ===\n")
        profiles = get_best_strategies(conn, limit=10)
        if not profiles:
            print("  No strategy profiles found. Run build_strategy_learning.py first.")
            return
        if args.json:
            print(json.dumps(profiles, indent=2, default=str))
            return
        for p in profiles:
            _print_profile(p)
        return

    if args.worst:
        print(f"\n=== Weak / Dangerous Strategies (bottom 10) ===\n")
        profiles = get_weak_strategies(conn, limit=10)
        if not profiles:
            print("  No strategy profiles found.")
            return
        if args.json:
            print(json.dumps(profiles, indent=2, default=str))
            return
        for p in profiles:
            _print_profile(p)
        return

    # Default: all profiles filtered
    profiles = get_strategy_profiles(
        conn,
        days=args.days,
        market_key=args.market or None,
        league_id=args.league,
    )
    print(f"\n=== Strategy Profiles ({len(profiles)} found) ===\n")
    if not profiles:
        print("  No profiles found. Run build_strategy_learning.py first.")
        return
    if args.json:
        print(json.dumps(profiles, indent=2, default=str))
        return
    for p in profiles[:20]:
        _print_profile(p)
    if len(profiles) > 20:
        print(f"  ... and {len(profiles) - 20} more. Use --json for full output.")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Report on strategy learning profiles")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--market", default="")
    p.add_argument("--league", type=int, default=None)
    p.add_argument("--best", action="store_true", help="Show top strategies")
    p.add_argument("--worst", action="store_true", help="Show weakest strategies")
    p.add_argument("--strategy-key", dest="strategy_key", default="",
                   help="Show detail for a specific strategy key")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    return p.parse_args()


if __name__ == "__main__":
    setup_logger()
    main(parse_args())
