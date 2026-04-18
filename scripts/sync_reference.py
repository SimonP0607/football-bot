#!/usr/bin/env python
"""Phase A + B reference sync — run once per season (or when adding new leagues).

Phase A: populate ref_bookmakers, ref_bet_types, validate timezone.
Phase B: discover currently-active leagues + their seasons, fetch coverage metadata.
         Stores the result in leagues.coverage so Phase C (sync_today) can skip
         endpoints that aren't available for each league.

Season resolution in Phase B (highest to lowest priority):
  1. Explicit --leagues 39:2025,140:2025 argument (all tokens must include season).
  2. Auto-discovery via /leagues?current=true, filtered to DEFAULT_LEAGUE_IDS.
  3. LEAGUE_SEASONS from .env applied as overrides on top of auto-discovered seasons.

Usage:
    # Phase A + B with full auto-discovery (recommended with Pro plan):
    python scripts/sync_reference.py

    # Phase A only:
    python scripts/sync_reference.py --phase a

    # Phase B only, auto-discover seasons for configured DEFAULT_LEAGUE_IDS:
    python scripts/sync_reference.py --phase b

    # Phase B with explicit seasons (skips auto-discovery):
    python scripts/sync_reference.py --phase b --leagues 39:2025,140:2025,253:2026

    # Both phases, explicit override for specific leagues:
    python scripts/sync_reference.py --leagues 39:2025,253:2026
"""

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.core.logger import setup_logger
    from app.services.sync_service import sync_service
except ImportError as _e:
    print(
        f"Error de importación: {_e}\n"
        "Asegúrate de activar el entorno virtual antes de ejecutar este script:\n"
        "  Windows:       .venv\\Scripts\\activate\n"
        "  macOS/Linux:   source .venv/bin/activate\n"
        "  o ejecuta directamente: .venv/Scripts/python.exe scripts/sync_reference.py"
    )
    sys.exit(1)


def _parse_explicit_leagues(leagues_arg: str) -> dict[int, int]:
    """Parse --leagues '39:2025,140:2025' into {39: 2025, 140: 2025}.

    Every token MUST include an explicit season (format: <id>:<year>).
    If any token lacks a season, the script exits with an error — use the
    auto-discovery mode (no --leagues) instead.
    """
    mapping: dict[int, int] = {}
    for token in leagues_arg.split(","):
        token = token.strip()
        if not token:
            continue
        if ":" not in token:
            print(
                f"Error: '{token}' no incluye season.\n"
                "  Usa el formato '<liga>:<season>', por ejemplo: 39:2025\n"
                "  O elimina --leagues para activar auto-discovery."
            )
            sys.exit(1)
        l_str, s_str = token.split(":", 1)
        if not l_str.strip().isdigit() or not s_str.strip().isdigit():
            print(f"Error: token inválido en --leagues: '{token}'")
            sys.exit(1)
        mapping[int(l_str.strip())] = int(s_str.strip())
    return mapping


async def main(phase: str, explicit_league_seasons: dict[int, int] | None) -> None:
    logger = logging.getLogger(__name__)

    if phase in ("a", "ab", "all"):
        logger.info("=== Phase A: Reference sync (bookmakers, bet types, timezone) ===")
        summary_a = await sync_service.sync_reference()
        logger.info("Phase A completada: %s", summary_a)

    if phase in ("b", "ab", "all"):
        if explicit_league_seasons:
            logger.info(
                "=== Phase B: Bootstrap coverage | leagues explícitas=%s ===",
                list(explicit_league_seasons.keys()),
            )
        else:
            logger.info(
                "=== Phase B: Bootstrap coverage | modo auto-discovery (/leagues?current=true) ==="
            )
        summary_b = await sync_service.sync_bootstrap(explicit_league_seasons)
        logger.info("Phase B completada: %s", summary_b)

        updated = summary_b.get("leagues_updated", 0)
        if updated > 0:
            logger.info(
                "Siguiente paso: python scripts/sync_today.py  (sin argumentos — auto-lee BD)"
            )
        else:
            logger.warning(
                "Phase B no actualizó ninguna liga. "
                "Verifica DEFAULT_LEAGUE_IDS en .env y que las ligas tengan temporada activa."
            )

    logger.info("=== Sync de referencia listo ===")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase A+B reference sync for API-Football.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Phase A: bookmakers + bet types + timezone validation. Always safe to re-run.\n"
            "Phase B: discover active leagues, fetch coverage metadata. Run once per season.\n\n"
            "Auto-discovery (recommended with Pro plan):\n"
            "  Set DEFAULT_LEAGUE_IDS in .env, then run without --leagues.\n"
            "  The service calls /leagues?current=true and resolves seasons automatically.\n\n"
            "Explicit seasons (override auto-discovery):\n"
            "  --leagues must include seasons for every league: 39:2025,253:2026\n\n"
            "Examples:\n"
            "  python scripts/sync_reference.py                          # auto-discover\n"
            "  python scripts/sync_reference.py --phase a                # Phase A only\n"
            "  python scripts/sync_reference.py --phase b                # Phase B only\n"
            "  python scripts/sync_reference.py --leagues 39:2025,140:2025  # explicit\n"
        ),
    )
    parser.add_argument(
        "--leagues",
        default=None,
        help=(
            "Explicit leagues with seasons: '39:2025,140:2025,253:2026'. "
            "Every token must include a season. "
            "Omit to auto-discover via /leagues?current=true."
        ),
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

    # Only parse --leagues for phases that need it
    explicit = None
    if args.phase in ("b", "ab", "all") and args.leagues:
        explicit = _parse_explicit_leagues(args.leagues)

    asyncio.run(main(args.phase, explicit))
