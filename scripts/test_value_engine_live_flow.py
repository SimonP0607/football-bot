#!/usr/bin/env python
"""Simula el flujo live del Value Engine contra pick_candidates actuales de Supabase.

Lee pick_candidates existentes (generados por sync_today.py / prediction_service),
aplica el value_engine_live_adapter y muestra tabla comparativa:

    fixture | liga | mercado | seleccion | old_edge | p_cal | fair_odds |
    offered_odds | edge_ve | ev_adj | quality | ve_status |
    decision_shadow | decision_assist

No modifica nada por defecto. Usa --write-metrics para guardar en pick_candidates.
Requiere: sync_today.py ya debe haber corrido para generar pick_candidates.

Usage:
    python scripts/test_value_engine_live_flow.py --mode shadow --no-write
    python scripts/test_value_engine_live_flow.py --mode assist --no-write
    python scripts/test_value_engine_live_flow.py --date 2026-04-27 --days 1 --mode shadow --no-write
    python scripts/test_value_engine_live_flow.py --mode shadow --write-metrics

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
    from app.data.repositories.supabase_client import get_supabase
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.services.value_engine_live_adapter import ValueEngineLiveAdapter, STATUS_SELECTED
    from app.data import supabase_reader
except ImportError as exc:
    print(f"Error de importacion: {exc}")
    sys.exit(2)


# ── Formatters ─────────────────────────────────────────────────────────────────


def _pct(v: float | None) -> str:
    return "  -- " if v is None else f"{v * 100:.1f}%"


def _o(v: float | None) -> str:
    return "  --" if v is None else f"{v:.2f}"


def _ev(v: float | None) -> str:
    return "   --  " if v is None else f"{v:+.4f}"


# ── Fake PredictionCandidate ───────────────────────────────────────────────────


class _FakeCandidate:
    """Reconstructs a PredictionCandidate-like object from a pick_candidates row."""

    def __init__(self, row: dict) -> None:
        self.fixture_id          = row["fixture_id"]
        self.market              = row["market_key"]
        self.selection           = row["selection"]
        self.model_probability   = float(row.get("model_probability") or 0)
        self.implied_probability = float(row.get("implied_probability") or 0)
        self.edge                = float(row.get("edge") or 0)
        self.confidence_score    = float(row.get("confidence_score") or 0)
        self.argument_json       = row.get("argument_json") or {}
        self.is_publishable      = bool(row.get("is_publishable", False))
        # best_odd stored in argument_json by OddsPredictor
        raw_best = self.argument_json.get("best_odd") or 0
        self.best_odd        = float(raw_best)
        self.best_bookmaker  = self.argument_json.get("best_bookmaker") or ""

    def __repr__(self) -> str:
        return (
            f"Candidate(fid={self.fixture_id}, {self.market}/{self.selection}, "
            f"edge={self.edge:.4f})"
        )


# ── Supabase helpers ───────────────────────────────────────────────────────────


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


def _get_candidates(fixture_ids: list[int]) -> list[dict]:
    if not fixture_ids:
        return []
    client = get_supabase()
    resp = (
        client.table("pick_candidates")
        .select("*")
        .in_("fixture_id", fixture_ids)
        .order("fixture_id")
        .execute()
    )
    return resp.data or []


def _update_value_metrics(fixture_id: int, market: str, selection: str, vr: dict) -> None:
    from datetime import timezone
    from app.data.repositories import prediction_repo
    prediction_repo.update_value_metrics(fixture_id, market, selection, vr)


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    p = argparse.ArgumentParser(
        description="Simula el flujo Value Engine contra pick_candidates actuales.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--date", type=str, default=None, metavar="YYYY-MM-DD",
                   help="Fecha base (default: hoy en timezone local).")
    p.add_argument("--days", type=int, default=1, metavar="N",
                   help="Dias hacia adelante (default: 1).")
    p.add_argument("--limit", type=int, default=50, metavar="N",
                   help="Maximo de fixtures a consultar (default: 50).")
    p.add_argument("--mode", choices=["shadow", "assist"], default="shadow",
                   help="Modo a simular (default: shadow).")
    p.add_argument("--no-write", action="store_true", default=True,
                   help="No escribir metricas en Supabase (default: True).")
    p.add_argument("--write-metrics", action="store_true",
                   help="Guardar value_engine_* en pick_candidates de Supabase.")
    args = p.parse_args()

    do_write = args.write_metrics and not args.no_write

    setup_logger()
    logging.getLogger("app").setLevel(logging.WARNING)
    logging.getLogger("app.services.shadow_service").setLevel(logging.WARNING)

    print(f"\n{'=' * 76}")
    print(f"  TEST VALUE ENGINE LIVE FLOW")
    print(f"  Modo     : {args.mode.upper()}")
    print(f"  Escritura: {'SI (--write-metrics)' if do_write else 'NO (dry-run)'}")
    print(f"{'=' * 76}")

    # ── DuckDB ─────────────────────────────────────────────────────────────────
    from pathlib import Path
    db_path = settings.value_engine_local_db_path
    if not Path(db_path).exists():
        print(f"\n  ERROR: DuckDB no encontrado en '{db_path}'")
        print(f"  Ejecuta: python scripts/init_local_db.py")
        sys.exit(1)

    conn = get_local_db(db_path)
    init_schema(conn)

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
    print(f"\n  Rango: {start_utc[:10]} -> {end_utc[:10]}  (limit={args.limit})")

    # ── Supabase: fixtures + candidates ────────────────────────────────────────
    print(f"\n  Consultando Supabase...")
    try:
        fixtures = _get_fixtures_for_range(start_utc, end_utc, args.limit)
    except Exception as exc:
        print(f"  ERROR conectando Supabase: {exc}")
        sys.exit(1)

    if not fixtures:
        print(f"  Sin fixtures en el rango.")
        print(f"  Ejecuta sync_today.py primero o ajusta --date / --days.")
        return

    fixture_ids  = [f["id"] for f in fixtures]
    fixture_map  = {f["id"]: f for f in fixtures}
    cand_rows    = _get_candidates(fixture_ids)

    if not cand_rows:
        print(f"  {len(fixtures)} fixtures pero sin pick_candidates.")
        print(f"  Ejecuta sync_today.py (que incluye prediction_service.run_for_today()).")
        return

    print(f"  {len(fixtures)} fixtures | {len(cand_rows)} pick_candidates")

    # ── ID mappings ─────────────────────────────────────────────────────────────
    print(f"  Cargando mappings (cs_map, team_map)...")
    cs_map   = supabase_reader.get_competition_season_map()
    team_ids = list({f["home_team_id"] for f in fixtures} | {f["away_team_id"] for f in fixtures})
    team_map = supabase_reader.get_team_provider_map(team_ids)

    # Resolve league names per fixture for display
    fixture_meta: dict[int, dict] = {}
    for fix in fixtures:
        cs_info   = cs_map.get(fix.get("league_id")) or {}
        prov_lid  = cs_info.get("provider_league_id")
        lg_name   = cs_info.get("league_name") or "?"
        home_info = team_map.get(fix.get("home_team_id")) or {}
        away_info = team_map.get(fix.get("away_team_id")) or {}
        home_name = home_info.get("name") or str(fix.get("home_team_id"))
        away_name = away_info.get("name") or str(fix.get("away_team_id"))
        fixture_meta[fix["id"]] = {
            "provider_league_id": prov_lid,
            "league_name": lg_name,
            "home_name": home_name,
            "away_name": away_name,
            "kickoff_at": fix.get("kickoff_at") or "",
        }

    # ── Build fresh adapter (bypasses VALUE_ENGINE_ENABLED check in settings) ───
    # The test script runs the engine regardless of .env config so the user can
    # evaluate results before activating it in production.
    adapter = ValueEngineLiveAdapter()
    adapter._bypass_mode_check = True  # force active without changing settings singleton

    fake_candidates = [_FakeCandidate(r) for r in cand_rows]

    # ── Evaluate ───────────────────────────────────────────────────────────────
    print(f"  Ejecutando value engine ({args.mode} mode)...")
    try:
        value_results = adapter.enrich_pick_candidates_with_value(fake_candidates, fixture_map)
    except Exception as exc:
        print(f"  ERROR en value engine: {exc}")
        sys.exit(1)

    if not value_results:
        print(f"  Value engine no devolvio resultados.")
        print(f"  Verifica que DuckDB tenga backtest y calibracion ejecutados.")
        return

    # For assist mode: determine which candidates survive the filter
    if args.mode == "assist":
        value_selected_set = {
            (c.fixture_id, c.market, c.selection)
            for c in adapter.rank_candidates_with_value(fake_candidates, value_results)
        }
    else:
        value_selected_set = set()

    # ── Comparison table ───────────────────────────────────────────────────────
    print(f"\n  TABLA COMPARATIVA ({len(fake_candidates)} candidatos)")
    hdr = (
        f"  {'fix_id':>8}  {'Liga':>5}  {'Mkt':<5}  {'Sel':<12}  "
        f"{'old_edge':>8}  {'old_pub':>7}  "
        f"{'p_cal':>6}  {'fair':>6}  {'odds':>6}  {'edge_ve':>7}  {'ev_adj':>7}  "
        f"{'quality':>7}  {'ve_status':<28}  shadow  assist"
    )
    print(hdr)
    print(f"  {'-' * (len(hdr) - 2)}")

    stats = {
        "total": len(fake_candidates),
        "evaluated": 0,
        "ve_selected": 0,
        "ve_low_quality": 0,
        "ve_low_edge": 0,
        "ve_missing_odds": 0,
        "ve_missing_hist": 0,
        "ve_missing_cal": 0,
        "ve_error": 0,
        "writes_ok": 0,
        "writes_err": 0,
    }

    for fc in fake_candidates:
        key = (fc.fixture_id, fc.market, fc.selection)
        vr  = value_results.get(key, {})
        vstatus = vr.get("value_engine_status", "value_skipped_engine_off")

        # Update stats
        if vstatus != "value_skipped_engine_off":
            stats["evaluated"] += 1
        if vstatus == "value_selected":             stats["ve_selected"] += 1
        elif "low_quality" in vstatus:              stats["ve_low_quality"] += 1
        elif "low_edge" in vstatus:                 stats["ve_low_edge"] += 1
        elif "missing_odds" in vstatus:             stats["ve_missing_odds"] += 1
        elif "missing_history" in vstatus:          stats["ve_missing_hist"] += 1
        elif "missing_calibrator" in vstatus:       stats["ve_missing_cal"] += 1
        elif "error" in vstatus:                    stats["ve_error"] += 1

        # Decision columns
        shadow_dec = "PICK" if fc.is_publishable else "---"
        assist_dec = "PICK" if key in value_selected_set else "---"

        meta = fixture_meta.get(fc.fixture_id, {})
        liga = str(meta.get("provider_league_id") or "?")

        print(
            f"  {fc.fixture_id:>8}  {liga:>5}  {fc.market:<5}  {fc.selection:<12}  "
            f"{fc.edge:>+8.4f}  {'SI' if fc.is_publishable else 'NO':>7}  "
            f"{_pct(vr.get('p_cal')):>6}  "
            f"{_o(vr.get('fair_odds')):>6}  "
            f"{_o(fc.best_odd if fc.best_odd > 1 else None):>6}  "
            f"{_ev(vr.get('edge')):>7}  "
            f"{_ev(vr.get('ev_adj')):>7}  "
            f"{(vr.get('quality_score') or 0):>7.4f}  "
            f"{vstatus:<28}  {shadow_dec:<6}  {assist_dec}"
        )

        # Optional write
        if do_write and vr and vstatus != "value_skipped_engine_off":
            try:
                _update_value_metrics(fc.fixture_id, fc.market, fc.selection, vr)
                stats["writes_ok"] += 1
            except Exception as exc:
                stats["writes_err"] += 1
                logger = logging.getLogger(__name__)
                logger.warning("Write error fid=%s %s/%s: %s", fc.fixture_id, fc.market, fc.selection, exc)

    # ── Summary ────────────────────────────────────────────────────────────────
    print(f"\n  RESUMEN")
    print(f"  Candidatos totales        : {stats['total']}")
    print(f"  Evaluados por value engine: {stats['evaluated']}")
    print(f"  value_selected            : {stats['ve_selected']}")
    print(f"  low_quality               : {stats['ve_low_quality']}")
    print(f"  low_edge                  : {stats['ve_low_edge']}")
    print(f"  missing_odds              : {stats['ve_missing_odds']}")
    print(f"  missing_history           : {stats['ve_missing_hist']}")
    print(f"  missing_calibrator        : {stats['ve_missing_cal']}")
    print(f"  errors                    : {stats['ve_error']}")

    if do_write:
        print(f"  Metricas guardadas        : {stats['writes_ok']}")
        print(f"  Errores al guardar        : {stats['writes_err']}")
    else:
        print(f"\n  DRY-RUN: nada fue escrito. Usa --write-metrics para guardar.")

    # ── Assist mode projection ─────────────────────────────────────────────────
    if args.mode == "assist":
        print(f"\n  PROYECCION MODO ASSIST")
        print(f"  Candidatos que pasarian value filter: {len(value_selected_set)}")
        current_pub = sum(1 for fc in fake_candidates if fc.is_publishable)
        print(f"  Publicables actuales (current model): {current_pub}")
        if len(value_selected_set) == 0 and settings.value_engine_fallback_to_current:
            print(f"  -> 0 value_selected: con FALLBACK_TO_CURRENT=true usaria modelo actual")
        elif len(value_selected_set) == 0:
            print(f"  -> 0 value_selected: con FALLBACK_TO_CURRENT=false = 0 picks")

    print(f"\n  Para activar en el flujo real, en .env:")
    print(f"    VALUE_ENGINE_ENABLED=true")
    print(f"    VALUE_ENGINE_MODE={args.mode}")
    print(f"  Luego ejecuta sync_today.py para que prediction_service use el value engine.")
    print(f"\n{'=' * 76}\n")


if __name__ == "__main__":
    main()
