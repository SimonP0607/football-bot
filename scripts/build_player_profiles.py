"""CLI: Build / refresh player season profiles and recent-form windows.

Usage:
  python scripts/build_player_profiles.py --dry-run
  python scripts/build_player_profiles.py --league 39 --season 2024 --execute
  python scripts/build_player_profiles.py --team 33 --season 2024 --execute
  python scripts/build_player_profiles.py --all --execute
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


def main(args: argparse.Namespace) -> None:
    from app.core.logger import setup_logger
    setup_logger()

    from app.data.local.duckdb_client import get_local_db, init_schema
    conn = get_local_db()
    init_schema(conn)

    dry_run = not args.execute
    season = args.season

    print("\n=== build_player_profiles ===")
    print(f"Mode:    {'EXECUTE' if args.execute else 'DRY-RUN'}")
    print(f"Season:  {season or '(current)'}")
    if args.league:
        print(f"League:  {args.league}")
    if args.team:
        print(f"Team:    {args.team}")
    if args.all:
        print("Scope:   ALL leagues/teams")
    print()

    from app.services.player_intelligence_service import (
        build_player_profiles,
        build_all_recent_forms,
    )

    profile_result = build_player_profiles(
        conn,
        league_id=args.league,
        season=season,
        team_id=args.team,
        dry_run=dry_run,
    )
    print(f"Profiles — status={profile_result['status']}")
    print(f"  Profiles:          {profile_result.get('profiles', 0)}")

    form_result = build_all_recent_forms(
        conn,
        windows=[3, 5, 10],
        league_id=args.league,
        dry_run=dry_run,
    )
    print(f"\nRecent-form — status={form_result['status']}")
    print(f"  Form records:      {form_result.get('form_records', form_result.get('players', 0))}")

    if dry_run:
        print("\n[dry-run] Sin cambios. Usa --execute para guardar.")
    print()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build player season profiles and recent-form windows.")
    p.add_argument("--league", type=int, default=None, help="Filter by league ID")
    p.add_argument("--season", type=int, default=None, help="Season year (e.g. 2024)")
    p.add_argument("--team", type=int, default=None, help="Filter by team ID")
    p.add_argument("--all", action="store_true", help="Process all leagues/teams")
    p.add_argument("--execute", action="store_true", help="Actually write to DB")
    return p.parse_args()


if __name__ == "__main__":
    main(parse_args())
