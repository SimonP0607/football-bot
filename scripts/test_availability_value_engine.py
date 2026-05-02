#!/usr/bin/env python
"""Test script: Availability-Aware Value Engine (Phase 5).

Loads today's fixtures and pick_candidates from Supabase, runs the Value
Engine adapter with availability enrichment enabled, and shows per-candidate
availability context and penalty/boost applied.

Read-only. Never writes to Supabase or modifies picks.

Usage:
    python scripts/test_availability_value_engine.py
    python scripts/test_availability_value_engine.py --days 2 --limit 20
    python scripts/test_availability_value_engine.py --date 2026-05-03
    python scripts/test_availability_value_engine.py --verbose
"""

from __future__ import annotations

import argparse
import io
import sys
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: F401 — loads .env


def _pct(v: float | None) -> str:
    if v is None:
        return "N/A"
    return f"{v * 100:.1f}%"


def _fmt(v: float | None, decimals: int = 4) -> str:
    if v is None:
        return "N/A"
    return f"{v:.{decimals}f}"


def main(args: argparse.Namespace) -> None:
    from app.data import supabase_reader
    from app.data.repositories.supabase_client import get_supabase
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.model.predictors.odds_predictor import PredictionCandidate
    from app.services.value_engine_live_adapter import ValueEngineLiveAdapter

    tz = ZoneInfo(settings.default_timezone)
    if args.date:
        base = datetime.fromisoformat(args.date).replace(tzinfo=tz)
    else:
        base = datetime.combine(datetime.now(tz).date(), dtime.min).replace(tzinfo=tz)
    end_dt = base + timedelta(days=args.days)
    start_utc = base.astimezone(ZoneInfo("UTC")).isoformat()
    end_utc   = end_dt.astimezone(ZoneInfo("UTC")).isoformat()

    print()
    print("=" * 70)
    print("  TEST AVAILABILITY VALUE ENGINE (Phase 5)")
    print(f"  Rango: {start_utc[:10]} -> {end_utc[:10]}")
    print(f"  use_availability: {settings.value_engine_use_availability}")
    print(f"  penalties h/m/l: "
          f"{settings.value_engine_availability_high_penalty}/"
          f"{settings.value_engine_availability_medium_penalty}/"
          f"{settings.value_engine_availability_low_penalty}")
    print(f"  boost opp h/m: "
          f"{settings.value_engine_availability_opponent_high_boost}/"
          f"{settings.value_engine_availability_opponent_medium_boost}")
    print(f"  cap: {settings.value_engine_availability_max_penalty}")
    print("=" * 70)

    # Load fixtures from Supabase
    client = get_supabase()
    fixtures_raw = (
        client.table("fixtures")
        .select("id, provider_fixture_id, league_id, home_team_id, away_team_id, kickoff_at")
        .gte("kickoff_at", start_utc)
        .lt("kickoff_at", end_utc)
        .order("kickoff_at")
        .limit(args.limit)
        .execute()
    ).data or []

    if not fixtures_raw:
        print("\n  Sin fixtures en Supabase para este rango.")
        print("  Ejecuta sync_today.py primero.")
        return

    fixture_ids = [f["id"] for f in fixtures_raw]
    fixture_map = {f["id"]: f for f in fixtures_raw}
    print(f"\n  Fixtures: {len(fixtures_raw)}")

    # Load candidates from Supabase
    candidates_raw = (
        client.table("pick_candidates")
        .select("fixture_id, market_key, selection, confidence_score, edge, model_probability, best_odd")
        .in_("fixture_id", fixture_ids)
        .execute()
    ).data or []

    if not candidates_raw:
        print("  Sin pick_candidates para este rango.")
        return
    print(f"  Candidatos: {len(candidates_raw)}")

    # Build PredictionCandidate objects
    all_candidates: list[PredictionCandidate] = []
    for c in candidates_raw:
        try:
            pc = PredictionCandidate(
                fixture_id=c["fixture_id"],
                market=c["market_key"],
                selection=c["selection"],
                confidence_score=c.get("confidence_score") or 0.0,
                edge=c.get("edge") or 0.0,
                model_probability=c.get("model_probability") or 0.0,
                implied_probability=0.0,
                best_odd=c.get("best_odd") or 0.0,
                best_bookmaker="",
                reasons=[],
                main_risk="",
            )
            all_candidates.append(pc)
        except Exception as exc:
            print(f"  ! PredictionCandidate error: {exc}")

    # Build adapter with bypass_mode_check=True
    adapter = ValueEngineLiveAdapter()
    adapter._bypass_mode_check = True

    # Init DuckDB
    conn = adapter._get_conn()
    if conn is None:
        print("\n  ERROR: DuckDB no disponible. Verifica VALUE_ENGINE_LOCAL_DB_PATH.")
        return
    init_schema(conn)

    # Run enrichment
    print(f"\n  Ejecutando Value Engine (modo=test, bypass_mode=True)...")
    results = adapter.enrich_pick_candidates_with_value(all_candidates, fixture_map)

    if not results:
        print("  Sin resultados del Value Engine.")
        return

    # Analyze
    n_total      = len(results)
    n_selected   = 0
    n_avail_data = 0
    n_penalized  = 0
    n_boosted    = 0
    n_rejected   = 0
    coverage_dist: dict[str, int] = {}
    impact_dist:   dict[str, int] = {}

    for key, vr in results.items():
        status = vr.get("value_engine_status", "")
        if status == "value_selected":
            n_selected += 1
        if status == "value_rejected_high_availability_risk":
            n_rejected += 1

        cov = vr.get("availability_coverage") or "unknown"
        coverage_dist[cov] = coverage_dist.get(cov, 0) + 1
        if cov == "data":
            n_avail_data += 1

        mod_imp = vr.get("availability_modeled_impact")
        if mod_imp:
            impact_dist[mod_imp] = impact_dist.get(mod_imp, 0) + 1

        pen = vr.get("availability_penalty") or 0.0
        bst = vr.get("availability_boost") or 0.0
        if pen > 0.0:
            n_penalized += 1
        if bst > 0.0:
            n_boosted += 1

    print(f"\n  RESUMEN")
    print(f"    Total candidatos evaluados  : {n_total}")
    print(f"    Pasaron VE (value_selected) : {n_selected}")
    print(f"    Con cobertura availability  : {n_avail_data}")
    print(f"    Con penalty aplicado        : {n_penalized}")
    print(f"    Con boost aplicado          : {n_boosted}")
    if settings.value_engine_reject_high_availability_risk:
        print(f"    Rechazados por avail risk   : {n_rejected}")
    print(f"    Distribucion cobertura:")
    for cov, cnt in sorted(coverage_dist.items()):
        print(f"      {cov:12}: {cnt}")
    if impact_dist:
        print(f"    Distribucion modeled_impact:")
        for imp in ("high", "medium", "low", "none", "unknown"):
            if impact_dist.get(imp):
                print(f"      {imp:10}: {impact_dist[imp]}")

    # Per-candidate detail (verbose or just penalized)
    show = [(k, v) for k, v in results.items()
            if args.verbose or (v.get("availability_penalty") or 0.0) > 0.0]

    if show:
        print(f"\n  DETALLE ({'todos' if args.verbose else 'solo penalizados'})")
        print(f"  {'fid':>8}  {'mkt':>6}  {'sel':>5}  {'cov':>8}  {'mod_imp':>8}  "
              f"{'opp_imp':>8}  {'q_before':>8}  {'penalty':>7}  {'boost':>5}  "
              f"{'q_after':>7}  status")
        print(f"  {'-' * 100}")
        for (fid, mkt, sel), vr in show:
            print(
                f"  {fid:>8}  {mkt:>6}  {sel:>5}  "
                f"{(vr.get('availability_coverage') or 'N/A'):>8}  "
                f"{(vr.get('availability_modeled_impact') or 'N/A'):>8}  "
                f"{(vr.get('availability_opponent_impact') or 'N/A'):>8}  "
                f"{_fmt(vr.get('quality_before_availability'), 4):>8}  "
                f"{_fmt(vr.get('availability_penalty'), 4):>7}  "
                f"{_fmt(vr.get('availability_boost'), 4):>5}  "
                f"{_fmt(vr.get('quality_score'), 4):>7}  "
                f"{vr.get('value_engine_status', '?')}"
            )
            if vr.get("availability_warning"):
                print(f"    -> {vr['availability_warning']}")

    print()
    print("=" * 70)
    print()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Test availability-aware value engine (Phase 5, read-only)",
    )
    p.add_argument("--date", type=str, default=None, metavar="YYYY-MM-DD",
                   help="Fecha base (default: hoy)")
    p.add_argument("--days", type=int, default=1, metavar="N",
                   help="Dias a revisar (default: 1)")
    p.add_argument("--limit", type=int, default=50, metavar="N",
                   help="Maximo de fixtures a consultar (default: 50)")
    p.add_argument("--verbose", action="store_true",
                   help="Mostrar todos los candidatos, no solo los penalizados")
    return p.parse_args()


if __name__ == "__main__":
    main(parse_args())
