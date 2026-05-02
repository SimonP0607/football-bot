#!/usr/bin/env python
"""Controlled enrichment refresh for today's fixtures.

Fetches updated standings, predictions, and lineups from API-Football for
fixtures that have already been loaded by sync_today.py. Useful when you want
to refresh context data without running a full sync.

Budget gates block each phase automatically based on daily quota remaining.

Phases:
  A  standings      — 1 call per league (priority: medium)
  B  predictions    — 1 call per fixture (priority: medium)
  C  lineups        — 1 call per fixture near kickoff only (priority: high)

Usage:
    python scripts/sync_enrichment_today.py              # dry-run (preview)
    python scripts/sync_enrichment_today.py --execute    # write to Supabase
    python scripts/sync_enrichment_today.py --execute --phase A
    python scripts/sync_enrichment_today.py --execute --phase B --limit 5
    python scripts/sync_enrichment_today.py --execute --phase C  # lineups only
    python scripts/sync_enrichment_today.py --execute --league 39
    python scripts/sync_enrichment_today.py --execute --max-requests 20
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
from app.services.api_budget_service import can_run, get_daily_state, record_call

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger("sync_enrichment_today")

_SCRIPT_NAME = "sync_enrichment_today"
_LINEUPS_WINDOW_MINUTES = 120  # fetch lineups if kickoff is within this many minutes


def _register_budget_hook() -> None:
    """Register the per-call budget logging hook in the API client."""
    try:
        from app.data.api_football.client import register_call_hook

        def _hook(endpoint: str, duration_ms: int, status_code: int, results: int) -> None:
            record_call(
                endpoint=endpoint,
                duration_ms=duration_ms,
                status_code=status_code,
                results_count=results,
                source_script=_SCRIPT_NAME,
                priority="medium",
            )

        register_call_hook(_hook)
    except Exception as exc:
        logger.debug("No se pudo registrar budget hook: %s", exc)


def _get_today_fixtures(league_filter: int | None) -> list[dict]:
    from app.data.repositories.fixture_repo import get_fixtures_today
    rows = get_fixtures_today()
    if league_filter:
        rows = [r for r in rows if r.get("league_id") == league_filter]
    return rows



def _resolve_cs_id(competition_season_id: int) -> dict | None:
    """Return {provider_league_id, season, coverage} for a competition_season.id."""
    try:
        from app.data.repositories.fixture_repo import get_competition_season_by_id
        return get_competition_season_by_id(competition_season_id)
    except Exception:
        return None


# ── Phase A: Standings ────────────────────────────────────────────────────────


async def _phase_a_standings(
    args: argparse.Namespace,
    fixtures: list[dict],
    request_counter: list[int],
) -> int:
    """Refresh standings for all leagues that have fixtures today.

    Returns number of leagues refreshed.
    """
    if not can_run("medium", _SCRIPT_NAME):
        state = get_daily_state()
        print(f"  [A] Bloqueado por presupuesto: {state['remaining']} restantes < 2000")
        return 0

    # Get unique competition_season IDs from today's fixtures
    league_ids = sorted({f["league_id"] for f in fixtures if f.get("league_id")})
    if not league_ids:
        print("  [A] Sin ligas en fixtures de hoy.")
        return 0

    if args.max_requests and request_counter[0] >= args.max_requests:
        print(f"  [A] Limite de llamadas alcanzado ({args.max_requests})")
        return 0

    from app.data.api_football.endpoints import fetch_standings
    from app.data.repositories.fixture_repo import update_fixture_context

    refreshed = 0
    for league_id in league_ids:
        if args.max_requests and request_counter[0] >= args.max_requests:
            print(f"  [A] Limite de llamadas alcanzado ({args.max_requests}) — parando")
            break

        cs_meta = _resolve_cs_id(league_id)
        if not cs_meta:
            print(f"  [A] cs_id={league_id}: sin metadatos — omitido")
            continue
        provider_league_id = cs_meta.get("provider_league_id")
        season = cs_meta.get("season") or settings.default_season
        if not provider_league_id or not season:
            print(f"  [A] cs_id={league_id}: provider_league_id o season desconocidos — omitido")
            continue

        print(f"  [A] standings league={provider_league_id} season={season} ...", end="", flush=True)

        if args.dry_run:
            print(" [DRY-RUN]")
            continue

        try:
            standings = await fetch_standings(provider_league_id, season)
            request_counter[0] += 1
        except Exception as exc:
            print(f" ERROR: {exc}")
            continue

        if not standings:
            print(" sin datos")
            continue

        # Update context_json for all fixtures in this league with new standings
        league_fixtures = [f for f in fixtures if f.get("league_id") == league_id]
        for fix in league_fixtures:
            fix_id = fix.get("id")
            if not fix_id:
                continue
            existing_ctx = fix.get("context_json") or {}
            # Build standing lookup by team provider_id
            def _find(team_id: int) -> dict | None:
                for s in standings:
                    if s.get("team", {}).get("id") == team_id:
                        return {
                            "rank": s.get("rank"),
                            "points": s.get("points"),
                            "played": s.get("all", {}).get("played"),
                            "wins": s.get("all", {}).get("win"),
                            "draws": s.get("all", {}).get("draw"),
                            "losses": s.get("all", {}).get("lose"),
                            "goals_for": s.get("all", {}).get("goals", {}).get("for"),
                            "goals_against": s.get("all", {}).get("goals", {}).get("against"),
                            "form": s.get("form"),
                        }
                return None

            h_id = (existing_ctx.get("home_team_provider_id")
                    or fix.get("home_team_provider_id"))
            a_id = (existing_ctx.get("away_team_provider_id")
                    or fix.get("away_team_provider_id"))
            if h_id:
                h_stand = _find(h_id)
                if h_stand:
                    existing_ctx["home_standing"] = h_stand
            if a_id:
                a_stand = _find(a_id)
                if a_stand:
                    existing_ctx["away_standing"] = a_stand

            if not args.dry_run:
                update_fixture_context(fix_id, existing_ctx)

        print(f" OK ({len(standings)} equipos, {len(league_fixtures)} fixtures actualizados)")
        refreshed += 1

    return refreshed


# ── Phase B: Predictions ──────────────────────────────────────────────────────


async def _phase_b_predictions(
    args: argparse.Namespace,
    fixtures: list[dict],
    request_counter: list[int],
) -> int:
    """Refresh provider predictions for today's upcoming fixtures."""
    if not can_run("medium", _SCRIPT_NAME):
        state = get_daily_state()
        print(f"  [B] Bloqueado por presupuesto: {state['remaining']} restantes < 2000")
        return 0

    from app.data.api_football.endpoints import fetch_provider_predictions
    from app.data.repositories.fixture_repo import update_fixture_context

    now = datetime.now(timezone.utc)
    refreshed = 0
    limit = args.limit or len(fixtures)
    candidates = [
        f for f in fixtures
        if (f.get("kickoff_at") or "") >= now.isoformat()[:10]
    ][:limit]

    if not candidates:
        print("  [B] Sin fixtures pendientes para actualizar predicciones.")
        return 0

    for fix in candidates:
        if args.max_requests and request_counter[0] >= args.max_requests:
            print(f"  [B] Limite de llamadas alcanzado ({args.max_requests}) — parando")
            break

        fix_id = fix.get("id")
        pfid = fix.get("provider_fixture_id")
        ko = (fix.get("kickoff_at") or "")[:16].replace("T", " ")
        if not fix_id or not pfid:
            continue

        cs_meta = _resolve_cs_id(fix.get("league_id", 0))
        coverage = (cs_meta or {}).get("coverage") or {}
        if not coverage.get("predictions"):
            print(f"  [B] fixture={pfid} ({ko}): predictions no disponibles en plan")
            continue

        print(f"  [B] predictions fixture={pfid} ({ko}) ...", end="", flush=True)

        if args.dry_run:
            print(" [DRY-RUN]")
            continue

        try:
            pred = await fetch_provider_predictions(pfid)
            request_counter[0] += 1
        except Exception as exc:
            print(f" ERROR: {exc}")
            continue

        if not pred:
            print(" sin datos")
            continue

        existing_ctx = fix.get("context_json") or {}
        existing_ctx["provider_prediction"] = pred
        update_fixture_context(fix_id, existing_ctx)
        print(" OK")
        refreshed += 1

    return refreshed


# ── Phase C: Lineups ──────────────────────────────────────────────────────────


async def _phase_c_lineups(
    args: argparse.Namespace,
    fixtures: list[dict],
    request_counter: list[int],
) -> int:
    """Fetch lineups for fixtures kicking off within the next 2 hours."""
    if not can_run("high", _SCRIPT_NAME):
        state = get_daily_state()
        print(f"  [C] Bloqueado por presupuesto: {state['remaining']} restantes < 1000")
        return 0

    from app.data.api_football.endpoints import fetch_lineups
    from app.data.repositories.fixture_repo import update_fixture_context

    now = datetime.now(timezone.utc)
    window_end = now + timedelta(minutes=_LINEUPS_WINDOW_MINUTES)

    candidates = []
    for f in fixtures:
        ko_str = f.get("kickoff_at") or ""
        if not ko_str:
            continue
        try:
            ko = datetime.fromisoformat(ko_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if now <= ko <= window_end:
            candidates.append(f)

    if not candidates:
        print(f"  [C] Sin fixtures con kickoff en los proximos {_LINEUPS_WINDOW_MINUTES} min.")
        return 0

    limit = args.limit or len(candidates)
    refreshed = 0

    for fix in candidates[:limit]:
        if args.max_requests and request_counter[0] >= args.max_requests:
            print(f"  [C] Limite de llamadas alcanzado ({args.max_requests}) — parando")
            break

        fix_id = fix.get("id")
        pfid = fix.get("provider_fixture_id")
        ko = (fix.get("kickoff_at") or "")[:16].replace("T", " ")
        if not fix_id or not pfid:
            continue

        print(f"  [C] lineups fixture={pfid} ({ko}) ...", end="", flush=True)

        if args.dry_run:
            print(" [DRY-RUN]")
            continue

        try:
            lineups = await fetch_lineups(pfid)
            request_counter[0] += 1
        except Exception as exc:
            print(f" ERROR: {exc}")
            continue

        if not lineups:
            print(" sin datos (alineaciones no publicadas aun)")
            continue

        existing_ctx = fix.get("context_json") or {}
        existing_ctx["lineups"] = lineups
        update_fixture_context(fix_id, existing_ctx)
        print(" OK")
        refreshed += 1

    return refreshed


# ── Main ──────────────────────────────────────────────────────────────────────


async def _main(args: argparse.Namespace) -> None:
    now = datetime.now(timezone.utc)
    _register_budget_hook()

    print()
    print("=" * 60)
    print("  SYNC ENRICHMENT TODAY")
    print(f"  {now.strftime('%Y-%m-%d %H:%M')} UTC")
    if args.dry_run:
        print("  MODO: DRY-RUN (sin escrituras a Supabase)")
    else:
        print("  MODO: EXECUTE (escribiendo a Supabase)")
    print("=" * 60)

    # Budget summary
    state = get_daily_state()
    remaining = state.get("remaining")
    limit_q = state.get("limit")
    status = state.get("status", "unknown")
    print()
    if remaining is not None:
        print(f"  Cuota API: {remaining}/{limit_q} restantes — {status.upper()}")
    else:
        print("  Cuota API: desconocida (ejecuta sync_today primero para leer headers)")

    if status == "critical" and not args.force:
        print("  !! Cuota CRITICA — abortando. Usa --force para ignorar.")
        print()
        return

    # Load today's fixtures
    print()
    print("  Cargando fixtures de hoy desde Supabase...")
    try:
        fixtures = _get_today_fixtures(args.league)
    except Exception as exc:
        print(f"  ERROR cargando fixtures: {exc}")
        return

    if not fixtures:
        msg = f"  Sin fixtures hoy"
        if args.league:
            msg += f" para league_id={args.league}"
        print(msg)
        print()
        print("=" * 60)
        print()
        return

    print(f"  {len(fixtures)} fixture(s) encontrado(s)")
    print()

    # Shared API call counter
    request_counter = [0]
    phases = args.phase.upper().split(",") if args.phase else ["A", "B", "C"]

    results: dict[str, int] = {}

    if "A" in phases:
        print("  -- Fase A: Standings --")
        results["A"] = await _phase_a_standings(args, fixtures, request_counter)
        print()

    if "B" in phases:
        print("  -- Fase B: Predictions --")
        # Reload fixtures to get fresh context_json after Phase A
        try:
            fixtures = _get_today_fixtures(args.league)
        except Exception:
            pass
        results["B"] = await _phase_b_predictions(args, fixtures, request_counter)
        print()

    if "C" in phases:
        print("  -- Fase C: Lineups --")
        try:
            fixtures = _get_today_fixtures(args.league)
        except Exception:
            pass
        results["C"] = await _phase_c_lineups(args, fixtures, request_counter)
        print()

    print("-" * 60)
    print(f"  Llamadas API realizadas: {request_counter[0]}")
    for phase, count in results.items():
        label = {"A": "Ligas standings", "B": "Predictions", "C": "Lineups"}[phase]
        print(f"  Fase {phase} ({label}): {count} actualizados")

    if args.dry_run:
        print()
        print("  DRY-RUN: sin cambios guardados.")
        print("  Ejecuta con --execute para aplicar.")

    print()
    print("=" * 60)
    print()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enriquecimiento controlado de fixtures de hoy"
    )
    parser.add_argument(
        "--execute", dest="dry_run", action="store_false",
        help="Aplicar cambios a Supabase (por defecto: dry-run)",
    )
    parser.set_defaults(dry_run=True)
    parser.add_argument(
        "--phase", metavar="PHASE",
        help="Fases a ejecutar: A, B, C o combinacion separada por comas (default: A,B,C)",
    )
    parser.add_argument(
        "--limit", type=int, metavar="N",
        help="Maximo de fixtures a procesar por fase",
    )
    parser.add_argument(
        "--league", type=int, metavar="ID",
        help="Solo fixtures de este league_id (competition_season.id)",
    )
    parser.add_argument(
        "--max-requests", type=int, metavar="N",
        help="Maximo total de llamadas API a realizar",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Ignorar estado CRITICO de cuota (usar con cuidado)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(_main(args))
