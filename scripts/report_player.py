"""CLI: Report on a player, team, or market signals.

Usage:
  python scripts/report_player.py --player "Salah"
  python scripts/report_player.py --player-id 306
  python scripts/report_player.py --team 33 --season 2024
  python scripts/report_player.py --market goals --limit 10
  python scripts/report_player.py --market shots --league 39 --limit 5
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

_MARKET_ALIASES: dict[str, str] = {
    "goals": "player_goal_signal",
    "assists": "player_assist_signal",
    "shots": "player_shot_signal",
    "shots_on_target": "player_shot_on_target_signal",
    "cards": "player_card_signal",
    "fouls": "player_foul_signal",
    "minutes": "player_minutes_signal",
}


def _section(title: str) -> None:
    print(f"\n{title}")
    print(_SEP)


def _fmt_float(v: object, decimals: int = 2) -> str:
    try:
        return f"{float(v):.{decimals}f}"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"


def report_player(conn: object, query: str | None, player_id: int | None) -> None:
    from app.services.player_intelligence_service import get_player_analysis

    lookup = query or str(player_id)
    result = get_player_analysis(conn, lookup)  # type: ignore[arg-type]

    profile = result.get("profile")
    form5 = result.get("form_5")
    history = result.get("history", [])

    _section(f"Player report — {lookup}")

    if not profile:
        print("  No profile found. Run sync + build_player_profiles first.")
        return

    print(f"  Name:            {profile.get('player_name', '—')}")
    print(f"  Position:        {profile.get('position', '—')}")
    print(f"  Team ID:         {profile.get('team_id', '—')}")
    print(f"  League ID:       {profile.get('league_id', '—')}")
    print(f"  Season:          {profile.get('season', '—')}")
    print(f"  Appearances:     {profile.get('appearances', 0)}")
    print(f"  Goals (avg):     {_fmt_float(profile.get('avg_goals'))}")
    print(f"  Assists (avg):   {_fmt_float(profile.get('avg_assists'))}")
    print(f"  Shots (avg):     {_fmt_float(profile.get('avg_shots'))}")
    print(f"  Minutes (avg):   {_fmt_float(profile.get('avg_minutes'))}")

    if form5:
        _section("Recent form (last 5 fixtures)")
        print(f"  Trend:           {form5.get('trend_label', '—')}")
        print(f"  Goals (avg):     {_fmt_float(form5.get('avg_goals'))}")
        print(f"  Assists (avg):   {_fmt_float(form5.get('avg_assists'))}")
        print(f"  Shots (avg):     {_fmt_float(form5.get('avg_shots'))}")
        print(f"  Minutes (avg):   {_fmt_float(form5.get('avg_minutes'))}")
        print(f"  Fixtures used:   {form5.get('n_matches', 0)}")

    if history:
        _section("Last fixtures")
        print(f"  {'fixture_id':<14} {'goals':>6} {'assists':>8} {'shots':>6} {'minutes':>8}")
        for h in history[:8]:
            print(
                f"  {h.get('provider_fixture_id', ''):<14}"
                f" {h.get('goals_scored', 0):>6}"
                f" {h.get('assists', 0):>8}"
                f" {h.get('shots_total', 0):>6}"
                f" {h.get('minutes_played', 0):>8}"
            )
    print()


def report_team(conn: object, team_id: int, season: int | None) -> None:
    from app.data.local.player_intelligence_repo import get_team_player_profiles

    _section(f"Team {team_id} — player profiles (season={season or 'latest'})")
    rows = get_team_player_profiles(conn, team_id, season=season, limit=25)  # type: ignore[arg-type]
    if not rows:
        print("  No profiles found for this team.")
        return
    print(f"  {'player':<25} {'pos':<5} {'apps':>5} {'G':>5} {'A':>5} {'shots':>6} {'min':>6}")
    for r in rows:
        print(
            f"  {(r.get('player_name') or ''):<25}"
            f" {(r.get('position') or ''):<5}"
            f" {r.get('appearances', 0):>5}"
            f" {_fmt_float(r.get('avg_goals'), 1):>5}"
            f" {_fmt_float(r.get('avg_assists'), 1):>5}"
            f" {_fmt_float(r.get('avg_shots'), 1):>6}"
            f" {_fmt_float(r.get('avg_minutes'), 0):>6}"
        )
    print()


def report_market(conn: object, market_key: str, league_id: int | None, limit: int) -> None:
    from app.data.local.player_intelligence_repo import get_top_players_by_market

    _section(f"Top players — {market_key} (league={league_id or 'all'}, limit={limit})")
    rows = get_top_players_by_market(conn, market_key, league_id=league_id, limit=limit)  # type: ignore[arg-type]
    if not rows:
        print("  No data. Run generate_player_signals first.")
        print("  NOTA: Señales analíticas. No son recomendaciones de apuesta.")
        return
    print(f"  {'player':<25} {'conf':>6}  {'trend':<15} {'status'}")
    for r in rows:
        print(
            f"  {(r.get('player_name') or ''):<25}"
            f" {_fmt_float(r.get('confidence_score'), 3):>6}"
            f"  {(r.get('trend_label') or ''):<15}"
            f" {r.get('status', '—')}"
        )
    print()
    print("  NOTA: Señales analíticas. No son recomendaciones de apuesta.")
    print()


def main(args: argparse.Namespace) -> None:
    from app.core.logger import setup_logger
    setup_logger()

    from app.data.local.duckdb_client import get_local_db, init_schema
    conn = get_local_db()
    init_schema(conn)

    if args.player or args.player_id:
        report_player(conn, args.player, args.player_id)
    elif args.team:
        report_team(conn, args.team, args.season)
    elif args.market:
        market_key = _MARKET_ALIASES.get(args.market, args.market)
        report_market(conn, market_key, args.league, args.limit)
    else:
        print("Specify --player, --player-id, --team, or --market. See --help.")
        sys.exit(1)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Report player intelligence from DuckDB.")
    p.add_argument("--player", type=str, default=None, help="Search by player name")
    p.add_argument("--player-id", type=int, default=None, help="Exact player ID")
    p.add_argument("--team", type=int, default=None, help="Show team roster profiles")
    p.add_argument("--season", type=int, default=None, help="Season year filter (with --team)")
    p.add_argument(
        "--market",
        type=str,
        default=None,
        choices=list(_MARKET_ALIASES.keys()) + list(_MARKET_ALIASES.values()),
        help="Market key for top-players report",
    )
    p.add_argument("--league", type=int, default=None, help="League filter (with --market)")
    p.add_argument("--limit", type=int, default=10, help="Max rows to show")
    return p.parse_args()


if __name__ == "__main__":
    main(parse_args())
