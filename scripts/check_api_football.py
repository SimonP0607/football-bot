#!/usr/bin/env python
"""Pre-flight check for API-Football connectivity.

Validates credentials and optionally fetches fixtures for a sample league.

Usage:
    python scripts/check_api_football.py
    python scripts/check_api_football.py --league 39 --season 2025

Exit codes:
    0 — connection and credentials OK
    1 — error (see output for details)
"""

import asyncio
import sys
import os
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()


def _check_env() -> bool:
    ok = True
    for var in ("API_FOOTBALL_BASE_URL", "API_FOOTBALL_KEY"):
        val = os.getenv(var, "")
        if not val or "<COMPLETAR>" in val:
            print(f"  ✗ {var} no está configurado en .env")
            ok = False
        else:
            masked = val[:15] + "..." if len(var) == "API_FOOTBALL_KEY" else val
            print(f"  ✓ {var} = {val if var == 'API_FOOTBALL_BASE_URL' else val[:8] + '...'}")
    return ok


async def _check_status(client) -> bool:
    """Call /status to verify credentials."""
    try:
        data = await client.get("/status")
        resp = data.get("response", {})
        account = resp.get("account", {})
        subscription = resp.get("subscription", {})
        requests = resp.get("requests", {})

        print(f"  ✓ Conectado correctamente")
        print(f"    Plan:              {subscription.get('plan', 'N/A')}")
        print(f"    Suscripción activa:{subscription.get('active', 'N/A')}")
        print(f"    Requests hoy:      {requests.get('current', 'N/A')} / {requests.get('limit_day', 'N/A')}")
        return True
    except Exception as exc:
        print(f"  ✗ Error al llamar /status: {exc}")
        print("    Verifica que API_FOOTBALL_KEY sea válida.")
        return False


async def _check_fixtures(client, league_id: int, season: int) -> bool:
    """Fetch today's fixtures for a sample league to verify data access."""
    from app.core.config import settings
    tz = ZoneInfo(settings.default_timezone)
    today = datetime.now(tz).strftime("%Y-%m-%d")

    print(f"\n  Consultando fixtures — league={league_id}, season={season}, date={today}")
    try:
        data = await client.get(
            "/fixtures",
            params={"date": today, "league": league_id, "season": season},
        )
        count = data.get("results", 0)
        fixtures = data.get("response", [])
        print(f"  ✓ Fixtures encontrados hoy: {count}")

        if fixtures:
            sample = fixtures[0]
            home = sample["teams"]["home"]["name"]
            away = sample["teams"]["away"]["name"]
            print(f"    Muestra: {home} vs {away}")

            # Check if odds are available for this fixture
            fixture_id = sample["fixture"]["id"]
            odds_data = await client.get("/odds", params={"fixture": fixture_id})
            odds_count = odds_data.get("results", 0)
            print(f"    Odds disponibles para ese fixture: {odds_count} bookmaker(s)")
        else:
            print("    (sin partidos hoy en esa liga — esto es normal fuera de jornada)")

        return True
    except Exception as exc:
        print(f"  ✗ Error al consultar fixtures: {exc}")
        return False


async def main(league_id: int | None, season: int | None) -> bool:
    print("\n=== check_api_football.py ===\n")

    print("[ 1 ] Variables de entorno")
    if not _check_env():
        print("\nCorrige .env antes de continuar.")
        return False

    print("\n[ 2 ] Conexión y credenciales (/status)")
    from app.data.api_football.client import api_client
    status_ok = await _check_status(api_client)

    fixtures_ok = True
    if status_ok and league_id and season:
        print("\n[ 3 ] Datos de ejemplo (fixtures + odds)")
        fixtures_ok = await _check_fixtures(api_client, league_id, season)
    elif status_ok:
        print(
            "\n[ 3 ] Datos de ejemplo — omitido\n"
            "    Pasa --league y --season para probar una consulta real:\n"
            "    python scripts/check_api_football.py --league 39 --season 2025"
        )

    print()
    if status_ok:
        print("✅  API-Football OK — credenciales válidas.")
    else:
        print("❌  Error de conexión. Revisa API_FOOTBALL_KEY en .env.")
    return status_ok and fixtures_ok


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check API-Football connectivity.")
    parser.add_argument("--league", type=int, default=None, help="League ID (e.g. 39)")
    parser.add_argument("--season", type=int, default=None, help="Season year (e.g. 2025)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    # Fall back to config defaults if not provided via CLI
    league_id = args.league
    season = args.season
    if not league_id or not season:
        try:
            from app.core.config import settings
            league_id = league_id or (settings.league_ids_list[0] if settings.league_ids_list else None)
            season = season or settings.default_season or None
        except Exception:
            pass

    success = asyncio.run(main(league_id, season))
    sys.exit(0 if success else 1)
