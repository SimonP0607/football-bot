#!/usr/bin/env python
"""Phase D prematch sync — run 60-90 minutes before kickoff.

Refreshes odds and fetches lineups for fixtures kicking off within a
configurable time window, then re-runs the prediction pipeline.

Usage:
    python scripts/sync_prematch.py                   # default: next 120 minutes
    python scripts/sync_prematch.py --window 90       # fixtures in next 90 min
    python scripts/sync_prematch.py --fixture 1060362 # specific fixture ID
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logger import setup_logger
from app.core.config import settings
from app.services.sync_service import sync_service
from app.services.prediction_service import prediction_service
from app.data.repositories.fixture_repo import (
    get_fixtures_today,
    get_fixture_by_provider_id,
)


def _fixtures_near_kickoff(window_minutes: int) -> list[int]:
    """Return internal IDs of today's fixtures kicking off within the window."""
    tz = ZoneInfo(settings.default_timezone)
    now = datetime.now(tz)
    cutoff = now + timedelta(minutes=window_minutes)

    fixtures = get_fixtures_today()
    result = []
    for fix in fixtures:
        kickoff_str = fix.get("kickoff_at", "")
        if not kickoff_str:
            continue
        try:
            kickoff = datetime.fromisoformat(kickoff_str).astimezone(tz)
        except (ValueError, TypeError):
            continue
        # Only include fixtures that haven't started yet and are within window
        status = fix.get("status_short") or fix.get("status", "")
        if status not in ("NS", "", None):
            continue  # already started or finished
        if now <= kickoff <= cutoff:
            result.append(fix["id"])
    return result


async def main(fixture_ids: list[int]) -> None:
    logger = logging.getLogger(__name__)

    if not fixture_ids:
        logger.info("Sin fixtures candidatos para prematch sync (ventana vacía)")
        return

    logger.info(
        "=== Phase D — Prematch sync | %d fixture(s) ===", len(fixture_ids)
    )

    sync_summary = await sync_service.sync_prematch(fixture_ids)
    logger.info("Phase D completada: %s", sync_summary)

    if sync_summary.get("odds_refreshed", 0) > 0:
        logger.info("=== Re-ejecutando pipeline de predicciones ===")
        pred_summary = prediction_service.run_for_today()
        logger.info("Predicciones: %s", pred_summary)

    logger.info("=== Prematch sync listo ===")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase D prematch sync: refresh odds + fetch lineups near kickoff.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Run this script 60-90 minutes before kickoff.\n\n"
            "Examples:\n"
            "  python scripts/sync_prematch.py\n"
            "  python scripts/sync_prematch.py --window 90\n"
            "  python scripts/sync_prematch.py --fixture 1060362\n"
        ),
    )
    parser.add_argument(
        "--window",
        default=120,
        type=int,
        help="Fetch fixtures kicking off within this many minutes (default: 120).",
    )
    parser.add_argument(
        "--fixture",
        default=None,
        type=int,
        help="Target a specific fixture by its API-Football provider ID.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    setup_logger()
    args = parse_args()

    logger = logging.getLogger(__name__)

    if args.fixture:
        fix = get_fixture_by_provider_id(args.fixture)
        if not fix:
            print(f"Error: fixture {args.fixture} no encontrado en la base de datos.")
            sys.exit(1)
        fixture_ids = [fix["id"]]
        logger.info("Modo fixture único: provider_id=%s → internal_id=%s", args.fixture, fix["id"])
    else:
        fixture_ids = _fixtures_near_kickoff(args.window)
        logger.info(
            "Fixtures en próximos %d min: %d encontrados", args.window, len(fixture_ids)
        )

    asyncio.run(main(fixture_ids))
