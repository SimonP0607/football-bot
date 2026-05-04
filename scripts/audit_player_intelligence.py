"""CLI: Audit the player intelligence module — tables, coverage, signals, health.

Usage:
  python scripts/audit_player_intelligence.py
  python scripts/audit_player_intelligence.py --league 39
  python scripts/audit_player_intelligence.py --verbose
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

_SEP = "-" * 60


def _row(label: str, value: object, width: int = 28) -> str:
    return f"  {label:<{width}} {value}"


def main(args: argparse.Namespace) -> None:
    from app.core.logger import setup_logger
    setup_logger()

    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.data.local.player_intelligence_repo import audit_player_intelligence

    conn = get_local_db()
    init_schema(conn)

    print(f"\n=== audit_player_intelligence ===")
    if args.league:
        print(f"League filter: {args.league}")
    print()

    # ── 1. Table counts ────────────────────────────────────────────────────────
    print("1. Tables")
    print(_SEP)
    counts = audit_player_intelligence(conn)
    for table, n in counts.items():
        print(_row(table, n))
    print()

    # ── 2. Player coverage ────────────────────────────────────────────────────
    print("2. Player coverage")
    print(_SEP)
    q_players = "SELECT COUNT(DISTINCT player_id) FROM player_fixture_stats"
    q_profiled = "SELECT COUNT(DISTINCT player_id) FROM player_season_profiles"
    q_formed = "SELECT COUNT(DISTINCT player_id) FROM player_recent_form"
    n_players = conn.execute(q_players).fetchone()[0]
    n_profiled = conn.execute(q_profiled).fetchone()[0]
    n_formed = conn.execute(q_formed).fetchone()[0]
    print(_row("Players with fixture stats", n_players))
    print(_row("Players with season profile", n_profiled))
    print(_row("Players with recent form", n_formed))
    print()

    # ── 3. League coverage ────────────────────────────────────────────────────
    print("3. League coverage (fixture stats)")
    print(_SEP)
    league_filter = f"WHERE league_id = {args.league}" if args.league else ""
    rows = conn.execute(
        f"SELECT league_id, COUNT(DISTINCT player_id) AS players, COUNT(*) AS rows "
        f"FROM player_fixture_stats {league_filter} "
        f"GROUP BY league_id ORDER BY rows DESC LIMIT 20"
    ).fetchall()
    if rows:
        print(f"  {'league_id':<12} {'players':>10} {'rows':>10}")
        for r in rows:
            print(f"  {r[0]:<12} {r[1]:>10} {r[2]:>10}")
    else:
        print("  (no fixture stats yet)")
    print()

    # ── 4. Team coverage ─────────────────────────────────────────────────────
    print("4. Team coverage (season profiles)")
    print(_SEP)
    team_filter = f"WHERE league_id = {args.league}" if args.league else ""
    rows = conn.execute(
        f"SELECT team_id, league_id, COUNT(DISTINCT player_id) AS players "
        f"FROM player_season_profiles {team_filter} "
        f"GROUP BY team_id, league_id ORDER BY players DESC LIMIT 20"
    ).fetchall()
    if rows:
        print(f"  {'team_id':<10} {'league_id':<12} {'players':>10}")
        for r in rows:
            print(f"  {r[0]:<10} {r[1]:<12} {r[2]:>10}")
    else:
        print("  (no profiles yet)")
    print()

    # ── 5. Signals overview ───────────────────────────────────────────────────
    print("5. Signals by market_key")
    print(_SEP)
    rows = conn.execute(
        "SELECT market_key, status, COUNT(*) AS n "
        "FROM player_prop_signals "
        "GROUP BY market_key, status ORDER BY market_key, status"
    ).fetchall()
    if rows:
        print(f"  {'market_key':<35} {'status':<14} {'count':>8}")
        for r in rows:
            print(f"  {r[0]:<35} {r[1]:<14} {r[2]:>8}")
    else:
        print("  (no signals yet)")
    print()

    # ── 6. Trend distribution ─────────────────────────────────────────────────
    print("6. Trend distribution (recent_form, window=5)")
    print(_SEP)
    rows = conn.execute(
        "SELECT trend_label, COUNT(*) AS n FROM player_recent_form "
        "WHERE window_size = 5 GROUP BY trend_label ORDER BY n DESC"
    ).fetchall()
    if rows:
        for r in rows:
            print(_row(r[0] or "(null)", r[1]))
    else:
        print("  (no recent-form rows yet)")
    print()

    # ── 7. Recent sync activity ───────────────────────────────────────────────
    print("7. Recent sync activity (last 5 fixture_stats inserts)")
    print(_SEP)
    rows = conn.execute(
        "SELECT provider_fixture_id, COUNT(*) AS players, MAX(synced_at) AS last_sync "
        "FROM player_fixture_stats GROUP BY provider_fixture_id "
        "ORDER BY last_sync DESC LIMIT 5"
    ).fetchall()
    if rows:
        print(f"  {'fixture_id':<14} {'players':>10}  last_sync")
        for r in rows:
            print(f"  {r[0]:<14} {r[1]:>10}  {r[2]}")
    else:
        print("  (no data yet)")
    print()

    # ── 8. High-confidence signals ────────────────────────────────────────────
    if args.verbose:
        print("8. Top signals by confidence (status=recommended, limit 10)")
        print(_SEP)
        rows = conn.execute(
            "SELECT player_name, market_key, confidence_score, trend_label, status "
            "FROM player_prop_signals WHERE status = 'recommended' "
            "ORDER BY confidence_score DESC LIMIT 10"
        ).fetchall()
        if rows:
            print(f"  {'player':<25} {'market':<30} {'conf':>6}  {'trend':<15} {'status'}")
            for r in rows:
                print(f"  {(r[0] or ''):<25} {r[1]:<30} {r[2]:>6.3f}  {(r[3] or ''):<15} {r[4]}")
        else:
            print("  (no recommended signals yet)")
        print()

    # ── 9. Recommendations ───────────────────────────────────────────────────
    print("9. Recommendations")
    print(_SEP)
    recs = []
    if counts.get("player_fixture_stats", 0) == 0:
        recs.append("Run: python scripts/sync_player_stats_today.py --days 3 --execute")
    if counts.get("player_season_profiles", 0) == 0:
        recs.append("Run: python scripts/build_player_profiles.py --execute")
    if counts.get("player_prop_signals", 0) == 0:
        recs.append("Run: python scripts/generate_player_signals.py --execute")
    if not recs:
        recs.append("Player intelligence module looks healthy.")
    for r in recs:
        print(f"  -> {r}")
    print()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Audit player intelligence data in DuckDB.")
    p.add_argument("--league", type=int, default=None, help="Limit coverage audit to one league")
    p.add_argument("--verbose", action="store_true", help="Show top signals table")
    return p.parse_args()


if __name__ == "__main__":
    main(parse_args())
