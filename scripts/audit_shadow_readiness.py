#!/usr/bin/env python
"""Fase A — Auditoría de readiness del pipeline shadow value.

Revisa el estado de DuckDB local + fixtures actuales en Supabase y reporta,
por cada partido, si está listo para predicción shadow con/sin odds.

Read-only para ambas bases de datos. No escribe nada.

Usage:
    python scripts/audit_shadow_readiness.py
    python scripts/audit_shadow_readiness.py --date 2025-05-01
    python scripts/audit_shadow_readiness.py --days 2 --limit 30
    python scripts/audit_shadow_readiness.py --show-all

Exit codes:
    0  OK
    2  import error
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.core.logger import setup_logger
    from app.data.local.duckdb_client import get_local_db
    from app.data import supabase_reader
except ImportError as e:
    print(f"Error de importacion: {e}")
    sys.exit(2)

_MARKETS = ["1X2", "DC", "OU25", "BTTS"]

# Status values (ordered from best to worst)
_STATUS_ORDER = [
    "ready_with_odds",
    "ready_without_odds",
    "missing_history",
    "missing_calibrator",
    "missing_team_mapping",
    "missing_league_mapping",
    "error",
]


def _yn(v: bool) -> str:
    return "SI" if v else "NO"


def _fixture_status(
    fixture: dict,
    cs_map: dict[int, dict],
    team_map: dict[int, dict],
    odds_map: dict[int, list],
    local: dict,
) -> dict:
    """Determine readiness status for one fixture."""
    fid          = fixture["id"]
    pfid         = fixture.get("provider_fixture_id")
    league_cs_id = fixture.get("league_id")
    home_tid     = fixture.get("home_team_id")
    away_tid     = fixture.get("away_team_id")
    ko           = fixture.get("kickoff_at", "")

    cs_info            = cs_map.get(league_cs_id) or {}
    provider_league_id = cs_info.get("provider_league_id")
    league_name        = cs_info.get("league_name") or "?"

    home_info       = team_map.get(home_tid) or {}
    away_info       = team_map.get(away_tid) or {}
    home_prov_id    = home_info.get("provider_team_id")
    away_prov_id    = away_info.get("provider_team_id")
    home_name       = home_info.get("name") or str(home_tid)
    away_name       = away_info.get("name") or str(away_tid)

    fixture_odds    = odds_map.get(fid, [])
    odds_markets    = sorted({o["market_key"] for o in fixture_odds if o["market_key"] in _MARKETS})

    # Eligibility checks in priority order
    if provider_league_id is None:
        status = "missing_league_mapping"
    elif home_prov_id is None or away_prov_id is None:
        status = "missing_team_mapping"
    elif provider_league_id not in local["leagues_with_samples"]:
        status = "missing_history"
    elif (
        provider_league_id not in local["leagues_with_calibrators"]
        and not local["has_global_calibrator"]
    ):
        status = "missing_calibrator"
    elif fixture_odds:
        status = "ready_with_odds"
    else:
        status = "ready_without_odds"

    return {
        "fixture_id":            fid,
        "provider_fixture_id":   pfid,
        "provider_league_id":    provider_league_id,
        "league_name":           league_name,
        "kickoff_at":            ko,
        "home_team":             home_name,
        "away_team":             away_name,
        "home_provider_team_id": home_prov_id,
        "away_provider_team_id": away_prov_id,
        "has_history":    provider_league_id in local["leagues_with_samples"]     if provider_league_id else False,
        "has_calibrator": (provider_league_id in local["leagues_with_calibrators"] or local["has_global_calibrator"]) if provider_league_id else False,
        "has_odds":       bool(fixture_odds),
        "supported_markets": odds_markets,
        "status":         status,
    }


def main() -> None:
    p = argparse.ArgumentParser(
        description="Fase A — Audit readiness del pipeline shadow value (read-only).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--date", type=str, default=None, metavar="YYYY-MM-DD",
                   help="Fecha base en UTC (default: inicio de hoy).")
    p.add_argument("--days", type=int, default=1, metavar="N",
                   help="Dias hacia adelante a revisar (default: 1).")
    p.add_argument("--limit", type=int, default=50, metavar="N",
                   help="Maximo de fixtures a consultar en Supabase (default: 50).")
    p.add_argument("--show-all", action="store_true",
                   help="Incluir fixtures con estado no-ready en la tabla detalle.")
    args = p.parse_args()

    setup_logger()
    logging.getLogger("app").setLevel(logging.WARNING)

    # ── Date range ─────────────────────────────────────────────────────────────
    if args.date:
        base = datetime.fromisoformat(args.date).replace(tzinfo=timezone.utc)
    else:
        base = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt    = base + timedelta(days=args.days)
    start_utc = base.isoformat()
    end_utc   = end_dt.isoformat()

    print(f"\n{'=' * 76}")
    print(f"  AUDIT SHADOW READINESS")
    print(f"  Rango : {start_utc[:10]} -> {end_utc[:10]}  (limit={args.limit})")
    print(f"{'=' * 76}")

    # ── DuckDB local state ─────────────────────────────────────────────────────
    conn  = get_local_db()
    local = supabase_reader.check_duckdb_readiness(conn)

    print(f"\n  ESTADO DUCKDB LOCAL")
    print(f"  {'Tabla':<28}  {'Filas':>8}")
    print(f"  {'-' * 40}")
    for key in ["fixtures_history", "training_samples", "team_elo_history",
                "calibration_registry", "market_quality_summary", "shadow_value_picks"]:
        n = local[key]
        flag = "  (ERROR)" if n == -1 else ""
        print(f"  {key:<28}  {n:>8}{flag}")
    print(f"  Ligas con training_samples  : {len(local['leagues_with_samples'])}")
    print(f"  Ligas con calibrador esp.   : {len(local['leagues_with_calibrators'])}")
    print(f"  Calibrador global activo    : {_yn(local['has_global_calibrator'])}"
          + (f"  ({', '.join(sorted(local['global_calibrator_scopes']))})" if local["global_calibrator_scopes"] else ""))

    # ── Supabase fixtures ──────────────────────────────────────────────────────
    print(f"\n  Consultando Supabase (fixtures)...")
    try:
        fixtures = supabase_reader.get_fixtures_for_range(start_utc, end_utc, limit=args.limit)
    except Exception as exc:
        print(f"  ERROR al conectar con Supabase: {exc}")
        sys.exit(1)

    if not fixtures:
        print(f"  Sin fixtures en Supabase para el rango indicado.")
        print(f"  Verifica la fecha, el rango de dias, o que sync_today.py se haya ejecutado.")
        return

    print(f"  {len(fixtures)} fixtures encontrados.")

    # ── Build mappings ─────────────────────────────────────────────────────────
    print(f"  Cargando mappings (competition_seasons, teams, odds)...")
    cs_map   = supabase_reader.get_competition_season_map()
    team_ids = list({f["home_team_id"] for f in fixtures} | {f["away_team_id"] for f in fixtures})
    team_map = supabase_reader.get_team_provider_map(team_ids)
    odds_map = supabase_reader.get_odds_for_fixtures([f["id"] for f in fixtures])

    # ── Per-fixture status ─────────────────────────────────────────────────────
    statuses = [_fixture_status(f, cs_map, team_map, odds_map, local) for f in fixtures]

    # Count by status
    from collections import Counter
    counts = Counter(s["status"] for s in statuses)

    print(f"\n  RESUMEN POR ESTADO")
    print(f"  {'Estado':<28}  {'N':>4}")
    print(f"  {'-' * 36}")
    for st in _STATUS_ORDER:
        n = counts.get(st, 0)
        if n or st.startswith("ready"):
            print(f"  {st:<28}  {n:>4}")

    ready_total = sum(counts.get(s, 0) for s in ["ready_with_odds", "ready_without_odds"])
    ready_odds  = counts.get("ready_with_odds", 0)

    # ── Fixture detail table ────────────────────────────────────────────────────
    to_show = [s for s in statuses if args.show_all or s["status"].startswith("ready")]
    to_show.sort(key=lambda x: (x["kickoff_at"], x["provider_league_id"] or 0))

    if to_show:
        print(f"\n  DETALLE FIXTURES {'(todos)' if args.show_all else '(solo listos)'}")
        header = (
            f"  {'fix_id':>8}  {'prov_fix':>8}  {'liga':>5}  "
            f"{'Partido':<30}  {'Kickoff':>16}  "
            f"{'Hist':>4}  {'Cal':>3}  {'Odds':>4}  "
            f"{'Mkt_odds':<16}  Status"
        )
        print(header)
        print(f"  {'-' * (len(header) - 2)}")
        for s in to_show:
            ko    = (s["kickoff_at"] or "")[:16].replace("T", " ")
            match = f"{s['home_team'][:13]} v {s['away_team'][:13]}"
            mkts  = ",".join(s["supported_markets"]) if s["supported_markets"] else "--"
            print(
                f"  {s['fixture_id']:>8}  "
                f"{str(s['provider_fixture_id'] or '?'):>8}  "
                f"{str(s['provider_league_id'] or '?'):>5}  "
                f"{match:<30}  {ko:>16}  "
                f"{_yn(s['has_history']):>4}  "
                f"{_yn(s['has_calibrator']):>3}  "
                f"{_yn(s['has_odds']):>4}  "
                f"{mkts:<16}  "
                f"{s['status']}"
            )

    # ── Conclusion ─────────────────────────────────────────────────────────────
    print(f"\n  CONCLUSIÓN")
    print(f"  Fixtures consultados  : {len(statuses)}")
    print(f"  Listos total          : {ready_total}  "
          f"({ready_odds} con odds, {ready_total - ready_odds} sin odds)")
    print(f"  Bloqueados mapping    : "
          f"{counts.get('missing_league_mapping', 0) + counts.get('missing_team_mapping', 0)}")
    print(f"  Sin histórico local   : {counts.get('missing_history', 0)}")
    print(f"  Sin calibrador        : {counts.get('missing_calibrator', 0)}")

    if ready_total > 0:
        print(f"\n  -> Ejecutar: python scripts/run_shadow_today.py --days {args.days} --limit {args.limit} --dry-run")
    else:
        print(f"\n  -> Sin fixtures listos. Verifica que backfill_history_api.py y run_calibration.py se hayan ejecutado.")
    print()


if __name__ == "__main__":
    main()
