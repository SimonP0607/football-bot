#!/usr/bin/env python
"""Auditoria del Value Engine en el flujo live de produccion.

Lee config, Supabase y DuckDB; reporta cuantos candidatos podrian ser
evaluados, cuantos tienen mapping completo, odds y calibrador activo.

Read-only para ambas bases de datos. No escribe nada.

Usage:
    python scripts/audit_value_engine_live.py
    python scripts/audit_value_engine_live.py --date 2026-04-27 --days 2 --limit 50

Exit codes:
    0  OK
    2  import error
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.core.logger import setup_logger
    from app.core.config import settings
    from app.data import supabase_reader
    from app.data.repositories.supabase_client import get_supabase
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.data.local.history_repo import get_competition_context
except ImportError as exc:
    print(f"Error de importacion: {exc}")
    sys.exit(2)


def _yn(v: bool) -> str:
    return "SI" if v else "NO"


def _pct(n: int, total: int) -> str:
    if total == 0:
        return "  --"
    return f"{n}/{total} ({n*100//total}%)"


def _check_duckdb(db_path: str) -> dict:
    from pathlib import Path
    result = {
        "exists": False,
        "conn": None,
        "error": None,
        "training_samples": 0,
        "calibration_registry": 0,
        "team_elo_history": 0,
        "competition_context": 0,
        "leagues_with_samples": set(),
        "leagues_with_calibrators": set(),
        "has_global_calibrator": False,
    }
    if not Path(db_path).exists():
        result["error"] = f"Archivo no encontrado: {db_path}"
        return result
    result["exists"] = True
    try:
        conn = get_local_db(db_path)
        init_schema(conn)
        result["conn"] = conn

        def _count(table: str) -> int:
            try:
                return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except Exception:
                return -1

        result["training_samples"]   = _count("training_samples")
        result["calibration_registry"] = _count("calibration_registry")
        result["team_elo_history"]   = _count("team_elo_history")
        result["competition_context"] = _count("competition_context")

        result["leagues_with_samples"] = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT provider_league_id FROM training_samples"
            ).fetchall()
        }
        result["leagues_with_calibrators"] = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT provider_league_id FROM calibration_registry "
                "WHERE provider_league_id IS NOT NULL"
            ).fetchall()
        }
        result["has_global_calibrator"] = bool(
            conn.execute(
                "SELECT 1 FROM calibration_registry WHERE provider_league_id IS NULL LIMIT 1"
            ).fetchone()
        )
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _get_fixtures_for_range(start_utc: str, end_utc: str, limit: int) -> list[dict]:
    client = get_supabase()
    resp = (
        client.table("fixtures")
        .select(
            "id, provider_fixture_id, league_id, home_team_id, away_team_id, "
            "kickoff_at, status_short"
        )
        .gte("kickoff_at", start_utc)
        .lt("kickoff_at", end_utc)
        .order("kickoff_at")
        .limit(limit)
        .execute()
    )
    return resp.data or []


def _get_candidates_for_fixtures(fixture_ids: list[int]) -> list[dict]:
    if not fixture_ids:
        return []
    client = get_supabase()
    resp = (
        client.table("pick_candidates")
        .select("fixture_id, market_key, selection, confidence_score, edge, is_publishable, argument_json")
        .in_("fixture_id", fixture_ids)
        .execute()
    )
    return resp.data or []


def main() -> None:
    p = argparse.ArgumentParser(
        description="Audit del Value Engine en flujo live (read-only).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--date", type=str, default=None, metavar="YYYY-MM-DD",
                   help="Fecha base en UTC (default: hoy en timezone local).")
    p.add_argument("--days", type=int, default=1, metavar="N",
                   help="Dias hacia adelante a revisar (default: 1).")
    p.add_argument("--limit", type=int, default=50, metavar="N",
                   help="Maximo de fixtures a consultar (default: 50).")
    args = p.parse_args()

    setup_logger()
    logging.getLogger("app").setLevel(logging.WARNING)

    print(f"\n{'=' * 76}")
    print(f"  AUDIT VALUE ENGINE LIVE")
    print(f"{'=' * 76}")

    # ── Config ─────────────────────────────────────────────────────────────────
    print(f"\n  CONFIG VALUE ENGINE")
    print(f"  VALUE_ENGINE_ENABLED     : {_yn(settings.value_engine_enabled)}")
    print(f"  VALUE_ENGINE_MODE        : {settings.value_engine_mode}")
    print(f"  VALUE_ENGINE_LOCAL_DB_PATH: {settings.value_engine_local_db_path}")
    print(f"  VALUE_ENGINE_MIN_QUALITY : {settings.value_engine_min_quality}")
    print(f"  VALUE_ENGINE_MIN_EDGE    : {settings.value_engine_min_edge}")
    print(f"  VALUE_ENGINE_MIN_EV_ADJ  : {settings.value_engine_min_ev_adj}")
    print(f"  VALUE_ENGINE_REQUIRE_ODDS: {_yn(settings.value_engine_require_odds)}")
    print(f"  VALUE_ENGINE_MAX_PER_LIGA: {settings.value_engine_max_picks_per_league}")
    print(f"  VALUE_ENGINE_FALLBACK    : {_yn(settings.value_engine_fallback_to_current)}")

    if not settings.value_engine_enabled:
        print(f"\n  [INFO] VALUE_ENGINE_ENABLED=false — el motor esta desactivado.")
        print(f"  Para activar en shadow: VALUE_ENGINE_ENABLED=true VALUE_ENGINE_MODE=shadow")

    print(f"\n  CONFIG DISPONIBILIDAD (PHASE 5)")
    print(f"  USE_AVAILABILITY         : {_yn(settings.value_engine_use_availability)}")
    if settings.value_engine_use_availability:
        print(f"  Penalty high / med / low : "
              f"{settings.value_engine_availability_high_penalty:.3f} / "
              f"{settings.value_engine_availability_medium_penalty:.3f} / "
              f"{settings.value_engine_availability_low_penalty:.3f}")
        print(f"  Boost opp_high / opp_med : "
              f"{settings.value_engine_availability_opponent_high_boost:.3f} / "
              f"{settings.value_engine_availability_opponent_medium_boost:.3f}")
        print(f"  Max penalty cap          : {settings.value_engine_availability_max_penalty:.3f}")
        print(f"  Reject high risk         : {_yn(settings.value_engine_reject_high_availability_risk)}")

    # ── DuckDB ─────────────────────────────────────────────────────────────────
    print(f"\n  ESTADO DUCKDB LOCAL")
    db = _check_duckdb(settings.value_engine_local_db_path)
    print(f"  Existe            : {_yn(db['exists'])}")
    if db["error"] and not db["exists"]:
        print(f"  Error             : {db['error']}")
    elif db["exists"]:
        if db["error"]:
            print(f"  Error conexion    : {db['error']}")
        else:
            print(f"  training_samples  : {db['training_samples']:>8}")
            print(f"  calibration_registry: {db['calibration_registry']:>6}")
            print(f"  team_elo_history  : {db['team_elo_history']:>8}")
            print(f"  competition_context: {db['competition_context']:>7}")
            print(f"  Ligas con samples : {len(db['leagues_with_samples'])}")
            print(f"  Ligas con calibrador especifico: {len(db['leagues_with_calibrators'])}")
            print(f"  Calibrador global : {_yn(db['has_global_calibrator'])}")

    # ── Date range ─────────────────────────────────────────────────────────────
    if args.date:
        base = datetime.fromisoformat(args.date).replace(
            tzinfo=ZoneInfo(settings.default_timezone)
        )
    else:
        tz   = ZoneInfo(settings.default_timezone)
        base = datetime.combine(datetime.now(tz).date(), time.min).replace(tzinfo=tz)
    end_dt    = base + timedelta(days=args.days)
    start_utc = base.astimezone(ZoneInfo("UTC")).isoformat()
    end_utc   = end_dt.astimezone(ZoneInfo("UTC")).isoformat()

    print(f"\n  RANGO: {start_utc[:10]} -> {end_utc[:10]}  (limit={args.limit})")

    # ── Supabase: fixtures ─────────────────────────────────────────────────────
    print(f"\n  Consultando Supabase (fixtures)...")
    try:
        fixtures = _get_fixtures_for_range(start_utc, end_utc, args.limit)
    except Exception as exc:
        print(f"  ERROR conectando Supabase: {exc}")
        sys.exit(1)

    if not fixtures:
        print(f"  Sin fixtures en el rango indicado.")
        print(f"  Ejecuta sync_today.py primero o cambia el rango de fechas.")
        return

    fixture_ids = [f["id"] for f in fixtures]
    print(f"  {len(fixtures)} fixtures encontrados.")

    # ── Supabase: mappings ─────────────────────────────────────────────────────
    print(f"  Cargando mappings (cs_map, team_map, odds)...")
    cs_map   = supabase_reader.get_competition_season_map()
    team_ids = list({f["home_team_id"] for f in fixtures} | {f["away_team_id"] for f in fixtures})
    team_map = supabase_reader.get_team_provider_map(team_ids)
    odds_map = supabase_reader.get_odds_for_fixtures(fixture_ids)

    # ── Supabase: pick_candidates ──────────────────────────────────────────────
    candidates = _get_candidates_for_fixtures(fixture_ids)
    cand_by_fixture: dict[int, list[dict]] = {}
    for c in candidates:
        cand_by_fixture.setdefault(c["fixture_id"], []).append(c)

    # ── Per-fixture diagnosis ──────────────────────────────────────────────────
    conn = db.get("conn")
    n_fixtures      = len(fixtures)
    n_has_mapping   = 0
    n_has_history   = 0
    n_has_calibrator= 0
    n_has_odds      = 0
    n_has_candidates= 0
    n_would_pass    = 0  # estimated candidates that might pass all filters

    rows = []
    for fix in fixtures:
        fid     = fix["id"]
        cs_info = cs_map.get(fix["league_id"]) or {}
        prov_lid= cs_info.get("provider_league_id")
        lg_name = cs_info.get("league_name") or "?"
        home_prov = (team_map.get(fix["home_team_id"]) or {}).get("provider_team_id")
        away_prov = (team_map.get(fix["away_team_id"]) or {}).get("provider_team_id")
        fix_odds  = odds_map.get(fid, [])
        fix_cands = cand_by_fixture.get(fid, [])

        has_mapping = prov_lid is not None and home_prov is not None and away_prov is not None
        has_history = (
            has_mapping
            and prov_lid in db["leagues_with_samples"]
        ) if has_mapping else False
        has_cal = (
            prov_lid in db["leagues_with_calibrators"] or db["has_global_calibrator"]
        ) if prov_lid else False
        has_odds = bool(fix_odds)
        has_cands= bool(fix_cands)

        if has_mapping:    n_has_mapping += 1
        if has_history:    n_has_history += 1
        if has_history and has_cal: n_has_calibrator += 1
        if has_odds:       n_has_odds += 1
        if has_cands:      n_has_candidates += 1

        # Context check
        context_ok = False
        if has_history and conn:
            try:
                ctx = get_competition_context(conn, prov_lid)
                context_ok = ctx is not None
            except Exception:
                pass

        ready = has_mapping and has_history and has_cal and context_ok
        if ready and fix_cands:
            n_would_pass += len(fix_cands)

        ko = (fix.get("kickoff_at") or "")[:16].replace("T", " ")
        rows.append({
            "fid":       fid,
            "ko":        ko,
            "liga":      prov_lid or "?",
            "lg_name":   lg_name[:20],
            "map":       has_mapping,
            "hist":      has_history,
            "cal":       has_cal,
            "ctx":       context_ok,
            "odds":      has_odds,
            "cands":     len(fix_cands),
            "ready":     ready,
        })

    # ── Summary ─────────────────────────────────────────────────────────────────
    print(f"\n  DIAGNOSTICO SUPABASE + DUCKDB")
    print(f"  Fixtures en rango         : {n_fixtures}")
    print(f"  Con mapping completo      : {_pct(n_has_mapping, n_fixtures)}")
    print(f"  Con historia en DuckDB    : {_pct(n_has_history, n_fixtures)}")
    print(f"  Con calibrador activo     : {_pct(n_has_calibrator, n_fixtures)}")
    print(f"  Con odds actuales         : {_pct(n_has_odds, n_fixtures)}")
    print(f"  Con pick_candidates       : {_pct(n_has_candidates, n_fixtures)}")
    print(f"  Candidatos evaluables (estimado): {n_would_pass}")

    # ── Availability coverage in DuckDB ───────────────────────────────────────
    if conn is not None and settings.value_engine_use_availability:
        try:
            prov_fixture_ids = [
                f["provider_fixture_id"] for f in fixtures
                if f.get("provider_fixture_id")
            ]
            if prov_fixture_ids:
                placeholders = ", ".join("?" * len(prov_fixture_ids))
                avail_rows = conn.execute(
                    f"""
                    SELECT provider_fixture_id, impact_label, coverage_status
                    FROM team_availability_summary
                    WHERE provider_fixture_id IN ({placeholders})
                    """,
                    prov_fixture_ids,
                ).fetchall()
                avail_fids = {r[0] for r in avail_rows}
                avail_with_data = {r[0] for r in avail_rows if r[2] == "data"}
                print(f"\n  DISPONIBILIDAD EN DUCKDB")
                print(f"  Fixtures con summary       : {_pct(len(avail_fids), len(prov_fixture_ids))}")
                print(f"  Fixtures con datos reales  : {_pct(len(avail_with_data), len(prov_fixture_ids))}")
                impact_counts: dict[str, int] = {}
                for r in avail_rows:
                    lbl = r[1] or "unknown"
                    impact_counts[lbl] = impact_counts.get(lbl, 0) + 1
                if impact_counts:
                    print(f"  Distribucion impact (team summaries):")
                    for lbl in ("high", "medium", "low", "none", "unknown"):
                        if impact_counts.get(lbl):
                            print(f"    {lbl:10}: {impact_counts[lbl]}")
        except Exception as exc:
            print(f"\n  DISPONIBILIDAD: tabla no encontrada o error ({exc})")

    # ── Fixture detail ─────────────────────────────────────────────────────────
    print(f"\n  DETALLE FIXTURES")
    print(f"  {'fix_id':>8}  {'Kickoff':>16}  {'Liga':>5}  {'Map':>3}  {'Hist':>4}  "
          f"{'Cal':>3}  {'Ctx':>3}  {'Odds':>4}  {'Cands':>5}  Status")
    print(f"  {'-' * 70}")
    for r in rows:
        status = "LISTO" if r["ready"] else (
            "sin_map"   if not r["map"]  else
            "sin_hist"  if not r["hist"] else
            "sin_cal"   if not r["cal"]  else
            "sin_ctx"   if not r["ctx"]  else "?"
        )
        print(
            f"  {r['fid']:>8}  {r['ko']:>16}  {str(r['liga']):>5}  "
            f"{_yn(r['map']):>3}  {_yn(r['hist']):>4}  "
            f"{_yn(r['cal']):>3}  {_yn(r['ctx']):>3}  "
            f"{_yn(r['odds']):>4}  {r['cands']:>5}  {status}"
        )

    # ── Conclusion ─────────────────────────────────────────────────────────────
    n_ready = sum(1 for r in rows if r["ready"])
    print(f"\n  CONCLUSION")
    print(f"  Fixtures listos para value engine: {n_ready}/{n_fixtures}")
    if n_ready == 0:
        print(f"\n  Sin fixtures listos. Posibles causas:")
        if n_has_mapping < n_fixtures:
            print(f"    - {n_fixtures - n_has_mapping} fixtures sin mapping (liga o equipos no mapeados)")
        if n_has_history < n_has_mapping:
            print(f"    - {n_has_mapping - n_has_history} fixtures sin historia en DuckDB")
            print(f"      Ejecuta: python scripts/backfill_history_api.py --league <ID>")
        if n_has_calibrator < n_has_history:
            print(f"    - {n_has_history - n_has_calibrator} fixtures sin calibrador")
            print(f"      Ejecuta: python scripts/run_calibration.py --all-leagues")
    else:
        mode = settings.value_engine_mode
        enabled = settings.value_engine_enabled
        print(f"\n  El value engine puede evaluar candidatos para {n_ready} fixtures.")
        if not enabled:
            print(f"\n  Para activar en modo shadow (no modifica picks):")
            print(f"    En .env: VALUE_ENGINE_ENABLED=true  VALUE_ENGINE_MODE=shadow")
            print(f"    Luego ejecuta sync_today.py para ver los logs de comparacion.")
        elif mode == "off":
            print(f"\n  VALUE_ENGINE_ENABLED=true pero VALUE_ENGINE_MODE=off.")
            print(f"  Cambia VALUE_ENGINE_MODE=shadow para ver metricas en logs.")
        elif mode == "shadow":
            print(f"\n  Modo shadow activo. Para probar sin el bot:")
            print(f"    python scripts/test_value_engine_live_flow.py --days {args.days} --limit {args.limit} --mode shadow --no-write")
        elif mode == "assist":
            print(f"\n  Modo assist activo. Para probar sin el bot:")
            print(f"    python scripts/test_value_engine_live_flow.py --days {args.days} --limit {args.limit} --mode assist --no-write")

    print(f"\n{'=' * 76}\n")


if __name__ == "__main__":
    main()
