#!/usr/bin/env python
"""CLI script to manually trigger today's sync + prediction pipeline.

Usage — explicit (override any config default):
    python scripts/sync_today.py --leagues 39,140 --season 2025

Usage — from config (uses DEFAULT_LEAGUE_IDS and DEFAULT_SEASON from .env):
    python scripts/sync_today.py

The script syncs fixtures and odds from API-Football into Supabase, then
runs the prediction pipeline and logs the results.  It does NOT start the
Telegram bot; it is meant to be run independently (e.g. as a cron job or
before launching the bot each day).
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


async def main(league_ids: list[int], season: int) -> None:
    setup_logger()
    logger = logging.getLogger(__name__)

    logger.info("=== Sync iniciado (leagues=%s, season=%s) ===", league_ids, season)
    sync_summary = await sync_service.sync_today(league_ids, season)
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
            "When --leagues or --season are omitted, values are read from\n"
            "DEFAULT_LEAGUE_IDS and DEFAULT_SEASON in .env.\n\n"
            "Examples:\n"
            "  python scripts/sync_today.py\n"
            "  python scripts/sync_today.py --leagues 39,140 --season 2025\n"
        ),
    )
    parser.add_argument(
        "--leagues",
        default=None,
        help=(
            "Comma-separated API-Football league IDs (e.g. 39,140). "
            "Defaults to DEFAULT_LEAGUE_IDS from .env."
        ),
    )
    parser.add_argument(
        "--season",
        default=None,
        type=int,
        help="Four-digit season year (e.g. 2025). Defaults to DEFAULT_SEASON from .env.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    # Resolve leagues — CLI arg takes priority; fall back to .env config
    if args.leagues:
        league_ids = [int(x.strip()) for x in args.leagues.split(",") if x.strip()]
    else:
        league_ids = settings.league_ids_list

    if not league_ids:
        print(
            "Error: no se especificaron ligas.\n"
            "Usa --leagues 39,140 o configura DEFAULT_LEAGUE_IDS en .env"
        )
        sys.exit(1)

    # Resolve season — CLI arg takes priority; fall back to .env config
    season = args.season or settings.default_season
    if not season:
        print(
            "Error: no se especificó la temporada.\n"
            "Usa --season 2025 o configura DEFAULT_SEASON en .env"
        )
        sys.exit(1)

    asyncio.run(main(league_ids, season))
