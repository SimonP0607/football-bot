#!/usr/bin/env python
"""Phase C daily sync — fixtures + context + odds + prediction pipeline.

Each configured league uses its own season (resolved automatically from the DB
after Phase B, or from explicit --leagues override).

Season resolution (highest to lowest priority):
  1. Explicit --leagues 39:2025,140:2025 argument.
  2. DB: leagues where current=True (populated by sync_reference --phase b).
  3. LEAGUE_SEASONS from .env applied as overrides on top of DB data.

Usage:
    # Auto-read active leagues from DB (recommended):
    python scripts/sync_today.py

    # Explicit leagues with seasons (override DB):
    python scripts/sync_today.py --leagues 39:2025,140:2025,253:2026

    # Override timezone:
    python scripts/sync_today.py --timezone America/Lima
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
    from app.services.prediction_service import prediction_service
except ImportError as _e:
    print(
        f"Error de importación: {_e}\n"
        "Asegúrate de activar el entorno virtual antes de ejecutar este script:\n"
        "  Windows:       .venv\\Scripts\\activate\n"
        "  macOS/Linux:   source .venv/bin/activate\n"
        "  o ejecuta directamente: .venv/Scripts/python.exe scripts/sync_today.py"
    )
    sys.exit(1)


def _parse_explicit_leagues(leagues_arg: str) -> dict[int, int]:
    """Parse --leagues '39:2025,140:2025' → {39: 2025, 140: 2025}.

    Every token must include an explicit season (format: <id>:<year>).
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
                "  O elimina --leagues para auto-leer de la BD (requiere Phase B previa)."
            )
            sys.exit(1)
        l_str, s_str = token.split(":", 1)
        if not l_str.strip().isdigit() or not s_str.strip().isdigit():
            print(f"Error: token inválido en --leagues: '{token}'")
            sys.exit(1)
        mapping[int(l_str.strip())] = int(s_str.strip())
    return mapping


def _register_budget_hook() -> None:
    try:
        from app.data.api_football.client import register_call_hook
        from app.services.api_budget_service import record_call

        def _hook(endpoint: str, duration_ms: int, status_code: int, results: int) -> None:
            record_call(
                endpoint=endpoint,
                duration_ms=duration_ms,
                status_code=status_code,
                results_count=results,
                source_script="sync_today",
                priority="high",
            )

        register_call_hook(_hook)
    except Exception:
        pass


async def main(league_seasons: dict[int, int] | None, timezone: str | None) -> None:
    logger = logging.getLogger(__name__)
    _register_budget_hook()

    if league_seasons:
        logger.info(
            "=== Phase C — leagues explícitas=%s | timezone=%s ===",
            league_seasons, timezone,
        )
    else:
        logger.info(
            "=== Phase C — auto-leyendo ligas activas de BD | timezone=%s ===", timezone
        )

    sync_summary = await sync_service.sync_daily(league_seasons, timezone=timezone)

    if sync_summary.get("error"):
        logger.error("Phase C abortada: %s", sync_summary)
        sys.exit(1)

    logger.info("Sync completado: %s", sync_summary)

    logger.info("=== Pipeline de predicciones ===")
    pred_summary = prediction_service.run_for_today()
    logger.info("Predicciones: %s", pred_summary)

    logger.info("=== Listo ===")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase C: sync today's fixtures + odds + run prediction pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Season resolution order:\n"
            "  1. --leagues explicit (e.g. 39:2025,253:2026)\n"
            "  2. DB: leagues.current=true (set by sync_reference --phase b)\n"
            "  3. LEAGUE_SEASONS from .env as overrides\n\n"
            "Examples:\n"
            "  python scripts/sync_today.py                          # auto-read DB\n"
            "  python scripts/sync_today.py --leagues 39:2025        # explicit\n"
            "  python scripts/sync_today.py --timezone America/Lima  # timezone override\n"
        ),
    )
    parser.add_argument(
        "--leagues",
        default=None,
        help=(
            "Explicit leagues with seasons: '39:2025,253:2026'. "
            "Omit to auto-read active leagues from the DB (set by Phase B)."
        ),
    )
    parser.add_argument(
        "--timezone",
        default=None,
        help="IANA timezone override (e.g. 'America/Bogota'). Defaults to DEFAULT_TIMEZONE.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    setup_logger()
    args = parse_args()

    league_seasons = _parse_explicit_leagues(args.leagues) if args.leagues else None
    asyncio.run(main(league_seasons, args.timezone))
