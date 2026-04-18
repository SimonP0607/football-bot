#!/usr/bin/env python
"""Phase A + B reference sync — run once per season (or when adding new leagues).

Phase A: populate ref_bookmakers, ref_bet_types, validate timezone.
Phase B: fetch league metadata + coverage so daily sync knows which endpoints
         are available for each league.

Usage:
    python scripts/sync_reference.py
    python scripts/sync_reference.py --leagues 39:2025,140:2025,253:2026
    python scripts/sync_reference.py --phase a        # Phase A only
    python scripts/sync_reference.py --phase b        # Phase B only
"""

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logger import setup_logger
from app.core.config import settings
from app.services.sync_service import sync_service


def _resolve_league_seasons(leagues_arg: str | None) -> dict[int, int]:
    """Parse --leagues arg or fall back to .env config."""
    if leagues_arg:
        mapping: dict[int, int] = {}
        for token in leagues_arg.split(","):
            token = token.strip()
            if not token:
                continue
            if ":" in token:
                l_str, s_str = token.split(":", 1)
                if l_str.strip().isdigit() and s_str.strip().isdigit():
                    mapping[int(l_str.strip())] = int(s_str.strip())
                else:
                    print(f"Error: token inválido en --leagues: '{token}'")
                    sys.exit(1)
            else:
                if not token.isdigit():
                    print(f"Error: ID de liga inválido: '{token}'")
                    sys.exit(1)
                league_id = int(token)
                season = (
                    settings.league_seasons_map.get(league_id)
                    or settings.default_season
                )
                if not season:
                    print(
                        f"Error: sin season para liga {league_id}. "
                        "Usa formato '39:2025' o configura LEAGUE_SEASONS en .env"
                    )
                    sys.exit(1)
                mapping[league_id] = season
        return mapping

    # Fall back to .env
    league_ids = settings.league_ids_list
    if not league_ids:
        print("Error: no hay ligas configuradas. Usa --leagues 39:2025,... o DEFAULT_LEAGUE_IDS en .env")
        sys.exit(1)

    seasons_map = settings.league_seasons_map
    mapping = {}
    for lid in league_ids:
        season = seasons_map.get(lid) or settings.default_season
        if not season:
            print(
                f"Error: sin season para liga {lid}. "
                "Configura LEAGUE_SEASONS o DEFAULT_SEASON en .env"
            )
            sys.exit(1)
        mapping[lid] = season
    return mapping


async def main(phase: str, league_seasons: dict[int, int]) -> None:
    logger = logging.getLogger(__name__)

    if phase in ("a", "ab", "all"):
        logger.info("=== Phase A: Reference sync (bookmakers, bet types, timezone) ===")
        summary_a = await sync_service.sync_reference()
        logger.info("Phase A completada: %s", summary_a)

    if phase in ("b", "ab", "all"):
        if not league_seasons:
            logger.warning("Phase B: sin ligas configuradas — omitida")
        else:
            logger.info(
                "=== Phase B: Bootstrap coverage sync | ligas=%s ===",
                list(league_seasons.keys()),
            )
            summary_b = await sync_service.sync_bootstrap(league_seasons)
            logger.info("Phase B completada: %s", summary_b)

    logger.info("=== Sync de referencia listo ===")
    logger.info(
        "Siguiente paso: python scripts/sync_today.py --leagues %s",
        ",".join(f"{lid}:{s}" for lid, s in league_seasons.items()),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase A+B reference sync for API-Football.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Phase A (bookmakers + bet types + timezone): always safe to re-run.\n"
            "Phase B (league coverage): run once per season or when adding leagues.\n\n"
            "Examples:\n"
            "  python scripts/sync_reference.py\n"
            "  python scripts/sync_reference.py --leagues 39:2025,253:2026\n"
            "  python scripts/sync_reference.py --phase a\n"
            "  python scripts/sync_reference.py --phase b --leagues 39:2025\n"
        ),
    )
    parser.add_argument(
        "--leagues",
        default=None,
        help="Comma-separated league IDs with seasons (e.g. '39:2025,253:2026').",
    )
    parser.add_argument(
        "--phase",
        default="ab",
        choices=["a", "b", "ab", "all"],
        help="Which phases to run (default: ab = both A and B).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    setup_logger()
    args = parse_args()
    league_seasons = _resolve_league_seasons(args.leagues) if args.phase in ("b", "ab", "all") else {}
    asyncio.run(main(args.phase, league_seasons))
