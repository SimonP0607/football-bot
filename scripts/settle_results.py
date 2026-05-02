#!/usr/bin/env python
"""Auto-settle pending picks using DuckDB history + API-Football fallback.

Resolution order for each pending fixture:
  1. DuckDB fixtures_history (free, covers historical seasons)
  2. API-Football /fixtures?ids=... (uses API quota, batched up to 20/call)

Only fetches from API when fixture kickoff + 2.5h has passed and DuckDB has no data.
Uses existing settlement_repo.settle() -- fully idempotent.

Usage:
    python scripts/settle_results.py                      # settle all pending
    python scripts/settle_results.py --days 7            # picks from last 7 days of kickoffs
    python scripts/settle_results.py --date 2026-04-24   # picks for a specific kickoff date
    python scripts/settle_results.py --fixture-id 1391145  # single provider fixture ID
    python scripts/settle_results.py --market 1X2        # only 1X2 picks
    python scripts/settle_results.py --dry-run           # preview without writing
    python scripts/settle_results.py --dry-run --days 3
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: F401 -- loads .env
from app.data.repositories import settlement_repo

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger("settle_results")

_FINISHED = {"FT", "AET", "PEN", "WO"}
_MIN_WAIT_AFTER_KICKOFF = timedelta(hours=2, minutes=30)


# -- Settlement resolution -----------------------------------------------------


def _outcome_from_duckdb(provider_fixture_id: int) -> dict | None:
    """Try to get goals from DuckDB fixtures_history.

    Returns {goals_home, goals_away, status_short, source='duckdb'} or None.
    Only returns data when the match is finished (status_short in _FINISHED).
    """
    try:
        import duckdb
        db = duckdb.connect(settings.local_db_path, read_only=True)
        row = db.execute(
            "SELECT goals_home, goals_away, status_short "
            "FROM fixtures_history WHERE id = ?",
            [provider_fixture_id],
        ).fetchone()
        db.close()
        if row and row[0] is not None and row[1] is not None and row[2] in _FINISHED:
            return {
                "goals_home": int(row[0]),
                "goals_away": int(row[1]),
                "status_short": row[2],
                "source": "duckdb",
            }
    except Exception as exc:
        logger.debug("DuckDB lookup failed for provider_id=%s: %s", provider_fixture_id, exc)
    return None


async def _fetch_results_from_api(provider_ids: list[int]) -> dict[int, dict]:
    """Fetch fixture statuses + goals from API-Football.

    Returns {provider_fixture_id: {goals_home, goals_away, status_short, source='api'}}.
    Only finished matches are included.
    """
    from app.data.api_football.endpoints import fetch_fixtures_by_ids
    items = await fetch_fixtures_by_ids(provider_ids)
    result: dict[int, dict] = {}
    for item in items:
        pfid = item.get("provider_fixture_id")
        status = item.get("status_short")
        gh = item.get("goals_home")
        ga = item.get("goals_away")
        if pfid and status in _FINISHED and gh is not None and ga is not None:
            result[pfid] = {
                "goals_home": int(gh),
                "goals_away": int(ga),
                "status_short": status,
                "source": "api",
            }
    return result


# -- Market resolution ---------------------------------------------------------


def _determine_outcome(market: str, selection: str, goals_home: int, goals_away: int) -> str:
    """Return 'win', 'loss', or 'void' for a pick given final scores."""
    total = goals_home + goals_away

    if market == "1X2":
        if selection == "Home":
            return "win" if goals_home > goals_away else "loss"
        if selection == "Draw":
            return "win" if goals_home == goals_away else "loss"
        if selection == "Away":
            return "win" if goals_away > goals_home else "loss"

    elif market == "OU25":
        if "Over" in selection:
            return "win" if total > 2 else "loss"
        if "Under" in selection:
            return "win" if total < 3 else "loss"

    elif market == "BTTS":
        both = goals_home > 0 and goals_away > 0
        if selection == "Yes":
            return "win" if both else "loss"
        if selection == "No":
            return "win" if not both else "loss"

    elif market == "DC":
        if selection in ("Home/Draw", "1X"):
            return "win" if goals_home >= goals_away else "loss"
        if selection in ("Away/Draw", "X2"):
            return "win" if goals_away >= goals_home else "loss"
        if selection in ("Home/Away", "12"):
            return "win" if goals_home != goals_away else "loss"

    logger.warning("Unknown market/selection: %s / %s -- marking void", market, selection)
    return "void"


def _profit(outcome: str, odd: float) -> float:
    if outcome == "win":
        return round(odd - 1.0, 4)
    if outcome == "loss":
        return -1.0
    return 0.0


# -- Main settle logic ---------------------------------------------------------


async def _settle_main(args: argparse.Namespace) -> None:
    now = datetime.now(timezone.utc)

    # Load pending picks enriched with fixture info
    all_pending = settlement_repo.get_all_pending_with_fixtures()
    if not all_pending:
        print("No hay picks pendientes de settlement.")
        return

    print(f"{len(all_pending)} picks pendientes en total.")

    # Apply filters
    pending = all_pending
    if args.fixture_id:
        pending = [
            p for p in pending
            if p.get("_fixture", {}).get("provider_fixture_id") == args.fixture_id
        ]
        if not pending:
            print(f"No hay picks pendientes para fixture provider_id={args.fixture_id}.")
            return

    if args.market:
        pending = [p for p in pending if p.get("market_key") == args.market]
        if not pending:
            print(f"No hay picks pendientes para market={args.market}.")
            return

    if args.date:
        date_prefix = args.date  # YYYY-MM-DD
        pending = [
            p for p in pending
            if (p.get("_fixture", {}).get("kickoff_at") or "").startswith(date_prefix)
        ]
        if not pending:
            print(f"No hay picks pendientes con kickoff en {args.date}.")
            return

    if args.days:
        cutoff = (now - timedelta(days=args.days)).isoformat()
        pending = [
            p for p in pending
            if (p.get("_fixture", {}).get("kickoff_at") or "9999") >= cutoff
        ]

    print(f"{len(pending)} picks elegibles tras filtros.")

    # Group by fixture for efficient API batching
    fixture_groups: dict[int, list[dict]] = {}
    skip_too_recent: list[int] = []
    provider_ids_for_duckdb: list[int] = []
    provider_ids_for_api: list[int] = []

    for pick in pending:
        fix = pick.get("_fixture", {})
        pfid = fix.get("provider_fixture_id")
        ko_str = fix.get("kickoff_at")
        if not pfid or not ko_str:
            logger.warning("pick_id=%s sin provider_fixture_id o kickoff_at", pick["id"])
            continue

        try:
            ko = datetime.fromisoformat(ko_str.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("kickoff_at invalido: %s", ko_str)
            continue

        if ko + _MIN_WAIT_AFTER_KICKOFF > now:
            skip_too_recent.append(pfid)
            continue

        fixture_groups.setdefault(pfid, []).append(pick)
        provider_ids_for_duckdb.append(pfid)

    if skip_too_recent:
        print(f"  Omitidos {len(skip_too_recent)} partidos (aun en juego o muy recientes)")

    if not fixture_groups:
        print("Ningun partido listo para settlement.")
        return

    # Resolution: DuckDB first, then API for the rest
    score_map: dict[int, dict] = {}

    for pfid in list(provider_ids_for_duckdb):
        result = _outcome_from_duckdb(pfid)
        if result:
            score_map[pfid] = result
        else:
            provider_ids_for_api.append(pfid)

    if provider_ids_for_api:
        print(f"  DuckDB: {len(score_map)} resultados  |  API: consultando {len(provider_ids_for_api)} fixtures...")
        api_results = await _fetch_results_from_api(provider_ids_for_api)
        score_map.update(api_results)
        still_missing = [pfid for pfid in provider_ids_for_api if pfid not in score_map]
        if still_missing:
            print(f"  Sin resultado disponible para {len(still_missing)} fixture(s): {still_missing[:5]}")
    else:
        print(f"  DuckDB: {len(score_map)} resultados (sin llamadas a API)")

    # Settle each eligible pick
    settled_count = 0
    skipped_count = 0

    print()
    print("-" * 64)
    for pfid, picks in fixture_groups.items():
        score = score_map.get(pfid)
        if not score:
            print(f"  fixture={pfid}: sin resultado -- omitido")
            skipped_count += len(picks)
            continue

        gh, ga = score["goals_home"], score["goals_away"]
        src = score["source"]

        for pick in picks:
            market = pick["market_key"]
            selection = pick["selection"]
            odd = float(pick["odd_taken"])
            outcome = _determine_outcome(market, selection, gh, ga)
            profit = _profit(outcome, odd)
            icon = "W" if outcome == "win" else ("L" if outcome == "loss" else "V")
            fix = pick.get("_fixture", {})
            ko_short = (fix.get("kickoff_at") or "")[:10]

            print(
                f"  [{icon}] fixture={pfid} {ko_short}  {market}/{selection}"
                f"  {gh}-{ga}  odd={odd:.2f}  profit={profit:+.4f}u  [{src}]"
            )

            if not args.dry_run:
                settlement_repo.settle(pick["pick_candidate_id"], outcome, profit)
                settled_count += 1
            else:
                settled_count += 1  # count as "would settle" in dry-run

    print("-" * 64)
    print()
    if args.dry_run:
        print(f"DRY-RUN: {settled_count} picks se resolverian, {skipped_count} omitidos.")
        print("Ejecuta sin --dry-run para guardar en Supabase.")
    else:
        print(f"Liquidados: {settled_count}  |  Sin resultado: {skipped_count}")
        if settled_count > 0:
            summary = settlement_repo.get_roi_summary()
            print()
            print("  ROI ACTUALIZADO")
            print(f"  Ganados: {summary['wins']}  Perdidos: {summary['losses']}  Anulados: {summary['voids']}")
            print(f"  Profit:  {summary['total_profit_units']:+.4f} u")
            if summary["settled"]:
                print(f"  ROI:     {summary['roi_pct']:+.2f}%")


# -- CLI -----------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto-settlement de picks pendientes")
    parser.add_argument("--days", type=int, metavar="N",
                        help="Solo liquidar partidos con kickoff en los ultimos N dias")
    parser.add_argument("--date", metavar="YYYY-MM-DD",
                        help="Solo liquidar partidos con kickoff en esta fecha")
    parser.add_argument("--fixture-id", type=int, metavar="ID",
                        help="Solo liquidar picks del fixture con este provider_fixture_id")
    parser.add_argument("--market", metavar="MARKET",
                        help="Solo liquidar picks de este mercado (1X2, OU25, BTTS)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Mostrar que se liquidaria sin escribir a Supabase")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(_settle_main(args))
