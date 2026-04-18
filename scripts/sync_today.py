#!/usr/bin/env python
"""CLI script to manually trigger today's sync + prediction pipeline.

Each league can use a different season year — necessary because European
leagues (2025) and Colombian leagues (2026) run on different calendars.

Usage — from config (reads DEFAULT_LEAGUE_IDS + LEAGUE_SEASONS from .env):
    python scripts/sync_today.py

Usage — explicit leagues with per-league season override:
    python scripts/sync_today.py --leagues 39:2025,140:2025,253:2026

Usage — explicit leagues with a single season fallback:
    python scripts/sync_today.py --leagues 39,140 --season 2025

Usage — explicit timezone:
    python scripts/sync_today.py --timezone America/Bogota
"""

import argparse
import asyncio
import logging
import sys
import os

# Ensure the project root is on sys.path when running as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logger import setup_logger
from app.core.config import settings
from app.services.sync_service import sync_service
from app.services.prediction_service import prediction_service


def _resolve_league_seasons(
    leagues_arg: str | None,
    season_arg: int | None,
) -> dict[int, int]:
    """Build the {league_id: season} mapping from CLI args + .env config.

    Priority (highest to lowest):
    1. --leagues with inline season notation (e.g. "39:2025,253:2026")
    2. --leagues without season + --season as a uniform fallback
    3. DEFAULT_LEAGUE_IDS + LEAGUE_SEASONS from .env
    4. DEFAULT_LEAGUE_IDS + DEFAULT_SEASON from .env as uniform fallback
    """
    logger = logging.getLogger(__name__)

    if leagues_arg:
        # Parse "39:2025,140:2025,253:2026" or plain "39,140,253"
        mapping: dict[int, int] = {}
        for token in leagues_arg.split(","):
            token = token.strip()
            if not token:
                continue
            if ":" in token:
                league_str, season_str = token.split(":", 1)
                if not league_str.strip().isdigit() or not season_str.strip().isdigit():
                    print(f"Error: token inválido en --leagues: '{token}'")
                    sys.exit(1)
                mapping[int(league_str.strip())] = int(season_str.strip())
            else:
                if not token.isdigit():
                    print(f"Error: ID de liga inválido en --leagues: '{token}'")
                    sys.exit(1)
                league_id = int(token)
                # Season from --season arg, then LEAGUE_SEASONS map, then DEFAULT_SEASON
                season = (
                    season_arg
                    or settings.league_seasons_map.get(league_id)
                    or settings.default_season
                )
                if not season:
                    print(
                        f"Error: no se pudo resolver la temporada para liga {league_id}.\n"
                        "Usa --leagues 39:2025 o configura LEAGUE_SEASONS / DEFAULT_SEASON en .env"
                    )
                    sys.exit(1)
                mapping[league_id] = season
        return mapping

    # No --leagues arg → use DEFAULT_LEAGUE_IDS from .env
    league_ids = settings.league_ids_list
    if not league_ids:
        print(
            "Error: no se especificaron ligas.\n"
            "Usa --leagues 39:2025,253:2026 o configura DEFAULT_LEAGUE_IDS en .env"
        )
        sys.exit(1)

    seasons_map = settings.league_seasons_map
    mapping = {}
    for league_id in league_ids:
        season = (
            season_arg
            or seasons_map.get(league_id)
            or settings.default_season
        )
        if not season:
            print(
                f"Error: no se pudo resolver la temporada para liga {league_id}.\n"
                "Configura LEAGUE_SEASONS (ej. 39:2025,253:2026) o DEFAULT_SEASON en .env"
            )
            sys.exit(1)
        mapping[league_id] = season

    logger.debug("league_seasons resuelto desde .env: %s", mapping)
    return mapping


async def main(league_seasons: dict[int, int], timezone: str | None) -> None:
    setup_logger()
    logger = logging.getLogger(__name__)

    logger.info(
        "=== Sync iniciado | ligas+seasons=%s | timezone=%s ===",
        league_seasons,
        timezone or settings.default_timezone,
    )

    sync_summary = await sync_service.sync_today(league_seasons, timezone=timezone)
    logger.info("Sync completado: %s", sync_summary)

    logger.info("=== Pipeline de predicciones ===")
    pred_summary = prediction_service.run_for_today()
    logger.info("Predicciones: %s", pred_summary)

    logger.info("=== Listo ===")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync fixtures + run predictions for today.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Season resolution order (per league):\n"
            "  1. Inline notation in --leagues (e.g. 253:2026)\n"
            "  2. --season (applied to all leagues without inline season)\n"
            "  3. LEAGUE_SEASONS in .env (e.g. 39:2025,253:2026)\n"
            "  4. DEFAULT_SEASON in .env as uniform fallback\n\n"
            "Examples:\n"
            "  python scripts/sync_today.py\n"
            "  python scripts/sync_today.py --leagues 39:2025,253:2026\n"
            "  python scripts/sync_today.py --leagues 39,140 --season 2025\n"
            "  python scripts/sync_today.py --timezone America/Lima\n"
        ),
    )
    parser.add_argument(
        "--leagues",
        default=None,
        help=(
            "Comma-separated league IDs, optionally with per-league season "
            "(e.g. '39:2025,253:2026'). Without season notation, --season or "
            "LEAGUE_SEASONS/.env are used as fallback."
        ),
    )
    parser.add_argument(
        "--season",
        default=None,
        type=int,
        help=(
            "Uniform season fallback for leagues without an explicit season. "
            "Overridden by per-league notation in --leagues or LEAGUE_SEASONS in .env."
        ),
    )
    parser.add_argument(
        "--timezone",
        default=None,
        help=(
            "IANA timezone string (e.g. 'America/Bogota'). "
            "Defaults to DEFAULT_TIMEZONE from .env."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    # Setup basic logging early so _resolve_league_seasons can log
    setup_logger()

    args = parse_args()
    league_seasons = _resolve_league_seasons(args.leagues, args.season)
    asyncio.run(main(league_seasons, args.timezone))
