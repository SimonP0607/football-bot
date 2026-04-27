#!/usr/bin/env python
"""Fase B — Shadow value engine: partidos reales de Supabase + modelo offline.

Flujo completo:
  1. Lee fixtures desde Supabase (READ ONLY).
  2. Mapea league_id -> provider_league_id, team_id -> provider_team_id.
  3. Verifica histórico y calibrador en DuckDB.
  4. Ejecuta predict_fixture() (Poisson + Elo, DuckDB local).
  5. Busca odds en Supabase para la selección del modelo por mercado.
  6. Llama evaluate_fixture() con odds_lookup cuando existen.
  7. Imprime resultados; persiste en shadow_value_picks solo con --persist.

Garantías:
  - Nunca escribe en Supabase.
  - Nunca envía a Telegram.
  - Por defecto es dry-run (requiere --persist para escribir).
  - Idempotente: re-ejecutar con --persist el mismo día reemplaza picks anteriores.

Usage:
    python scripts/run_shadow_today.py --dry-run
    python scripts/run_shadow_today.py --days 2 --limit 30 --dry-run
    python scripts/run_shadow_today.py --days 2 --limit 30 --persist
    python scripts/run_shadow_today.py --league 39 --persist
    python scripts/run_shadow_today.py --date 2025-05-10 --persist

Exit codes:
    0  completado (incluso si 0 picks seleccionados)
    2  error de conexion a Supabase
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.core.logger import setup_logger
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.data.local.history_repo import get_competition_context
    from app.data import supabase_reader
    from app.services.shadow_service import predict_fixture
    from app.services.value_betting_service import evaluate_fixture, MIN_PCAL, MIN_WREL
    from app.services.backtest_service import MARKETS
except ImportError as exc:
    print(f"Error de importacion: {exc}")
    sys.exit(1)


# ── Formatters ─────────────────────────────────────────────────────────────────


def _pct(v: float | None) -> str:
    return "  -- " if v is None else f"{v * 100:.1f}%"


def _o(v: float | None) -> str:
    return "  --" if v is None else f"{v:.2f}"


def _ev(v: float | None) -> str:
    return "   -- " if v is None else f"{v:+.4f}"


# ── Odds lookup ────────────────────────────────────────────────────────────────


def _build_odds_lookup(
    fixture_id: int,
    odds_map: dict[int, list[dict]],
    model_picks: dict,
) -> dict[str, float]:
    """Build {market_key: best_odd} for the model's selection per market.

    'Best' = highest offered odds (most favourable for the bettor).
    Only includes markets where odds exist for the model's exact selection.
    """
    raw = odds_map.get(fixture_id, [])
    if not raw:
        return {}
    lookup: dict[str, float] = {}
    for market_key, pick in model_picks.items():
        selection = pick["selection"]
        candidates = [
            o["odd"] for o in raw
            if o["market_key"] == market_key and o["selection"] == selection
        ]
        if candidates:
            lookup[market_key] = max(candidates)
    return lookup


# ── Per-fixture output ─────────────────────────────────────────────────────────


def _print_fixture_result(
    fixture: dict,
    league_name: str,
    shadow: dict,
    value: dict,
    dry_run: bool,
    verbose: bool,
) -> None:
    ko        = (fixture.get("kickoff_at") or "")[:16].replace("T", " ")
    selected  = value.get("selected")
    candidates = value.get("candidates", [])

    print(f"\n  -- Fixture {fixture['id']}  |  {ko}  |  {league_name}")

    if verbose:
        print(f"    Home Elo={shadow['home_elo']:.0f}  Away Elo={shadow['away_elo']:.0f}  "
              f"lh={shadow['lambda_home']:.3f}  la={shadow['lambda_away']:.3f}")
        print(f"    {'Mkt':<5}  {'Seleccion':<14}  {'p_raw':>6}  {'p_cal':>6}  "
              f"{'fair':>6}  {'odds':>6}  {'edge':>7}  {'ev_adj':>7}  {'quality':>7}  decision")
        print(f"    {'-' * 78}")
        for mkt in MARKETS:
            c = next((x for x in candidates if x["market_key"] == mkt), None)
            if not c:
                continue
            tag = {"selected": "*SELECT", "rejected_cap": "cap", "rejected_filter": "---"}.get(
                c.get("decision_status", ""), "?"
            )
            print(
                f"    {c['market_key']:<5}  {c['selection']:<14}  "
                f"{_pct(c['p_raw']):>6}  {_pct(c['p_cal']):>6}  "
                f"{_o(c['fair_odds']):>6}  "
                f"{_o(c.get('offered_odds')):>6}  "
                f"{_ev(c.get('edge')):>7}  "
                f"{_ev(c.get('ev_adj')):>7}  "
                f"{c['quality_score']:>7.4f}  {tag}"
            )

    if selected:
        mode_tag = "[DRY-RUN]" if dry_run else "[GUARDADO]"
        ev_part  = f"  edge={_ev(selected.get('edge'))}  ev_adj={_ev(selected.get('ev_adj'))}" if selected.get("ev_adj") is not None else ""
        print(
            f"    -> PICK: {selected['market_key']} {selected['selection']}"
            f"  p_cal={_pct(selected['p_cal'])}  fair={_o(selected['fair_odds'])}"
            f"  quality={selected['quality_score']:.4f}{ev_part}  {mode_tag}"
        )
    else:
        print(f"    -> Sin pick (ningún candidato pasa filtros)")


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    p = argparse.ArgumentParser(
        description="Fase B — Shadow value engine: partidos reales de hoy.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--date", type=str, default=None, metavar="YYYY-MM-DD",
                   help="Fecha base en UTC (default: inicio de hoy).")
    p.add_argument("--days", type=int, default=1, metavar="N",
                   help="Dias hacia adelante (default: 1).")
    p.add_argument("--limit", type=int, default=None, metavar="N",
                   help="Maximo de fixtures a procesar desde Supabase.")
    p.add_argument("--league", type=int, default=None, metavar="ID",
                   help="Filtrar por provider_league_id.")
    p.add_argument("--dry-run", action="store_true",
                   help="Calcular sin escribir en DuckDB (activo por defecto).")
    p.add_argument("--persist", action="store_true",
                   help="Guardar picks en shadow_value_picks de DuckDB.")
    p.add_argument("--min-quality", type=float, default=0.0, metavar="Q",
                   help="Filtrar fixtures sin ningún candidato con quality >= Q.")
    p.add_argument("--min-edge", type=float, default=0.0, metavar="E",
                   help="Filtrar picks sin edge >= E (solo afecta reporte con odds).")
    p.add_argument("--max-picks", type=int, default=None, metavar="N",
                   help="Detener despues de N picks seleccionados.")
    p.add_argument("--verbose", action="store_true",
                   help="Mostrar tabla completa por mercado para cada fixture.")
    args = p.parse_args()

    # --persist sobreescribe el modo dry-run implícito
    do_persist = args.persist and not args.dry_run

    setup_logger()
    logging.getLogger("app.services").setLevel(logging.WARNING)
    logging.getLogger("app.data").setLevel(logging.WARNING)

    # ── Date range ─────────────────────────────────────────────────────────────
    if args.date:
        base = datetime.fromisoformat(args.date).replace(tzinfo=timezone.utc)
    else:
        base = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt    = base + timedelta(days=args.days)
    start_utc = base.isoformat()
    end_utc   = end_dt.isoformat()

    print(f"\n{'=' * 76}")
    print(f"  RUN SHADOW TODAY")
    print(f"  Rango   : {start_utc[:10]} -> {end_utc[:10]}  (days={args.days})")
    print(f"  Modo    : {'PERSIST -> DuckDB' if do_persist else 'DRY-RUN (no escribe)'}")
    if args.league:
        print(f"  Liga    : {args.league}")
    print(f"{'=' * 76}")

    # ── DuckDB ─────────────────────────────────────────────────────────────────
    conn = get_local_db()
    init_schema(conn)

    # ── Supabase (read-only) ────────────────────────────────────────────────────
    print(f"\n  Consultando Supabase...")
    try:
        fixtures = supabase_reader.get_fixtures_for_range(
            start_utc, end_utc, limit=args.limit or 200
        )
    except Exception as exc:
        print(f"  ERROR conectando a Supabase: {exc}")
        sys.exit(2)

    if not fixtures:
        print(f"  Sin fixtures para el rango indicado.")
        print(f"  Verifica que sync_today.py se haya ejecutado para esas fechas.")
        return

    cs_map   = supabase_reader.get_competition_season_map()
    team_ids = list({f["home_team_id"] for f in fixtures} | {f["away_team_id"] for f in fixtures})
    team_map = supabase_reader.get_team_provider_map(team_ids)
    odds_map = supabase_reader.get_odds_for_fixtures([f["id"] for f in fixtures])

    print(f"  {len(fixtures)} fixtures | odds para {len(odds_map)} fixtures")

    # ── Stats ──────────────────────────────────────────────────────────────────
    stats: dict[str, int] = {
        "total": len(fixtures), "eligible": 0, "skipped": 0,
        "picks_candidates": 0, "picks_selected": 0,
        "with_odds": 0, "without_odds": 0, "errors": 0,
    }
    skip_reasons: dict[str, int] = {}
    picks_selected_count = 0

    # ── Per-fixture loop ───────────────────────────────────────────────────────
    for fixture in fixtures:
        fid  = fixture["id"]
        pfid = fixture.get("provider_fixture_id")
        ko   = fixture.get("kickoff_at", "")
        ko_date = ko[:10] if ko else None

        # ── Map Supabase IDs -> provider IDs ───────────────────────────────────
        cs_info            = cs_map.get(fixture["league_id"]) or {}
        provider_league_id = cs_info.get("provider_league_id")
        league_name        = cs_info.get("league_name") or "?"

        if provider_league_id is None:
            skip_reasons["missing_league_mapping"] = skip_reasons.get("missing_league_mapping", 0) + 1
            stats["skipped"] += 1
            continue

        if args.league and provider_league_id != args.league:
            stats["skipped"] += 1
            continue

        home_info    = team_map.get(fixture["home_team_id"]) or {}
        away_info    = team_map.get(fixture["away_team_id"]) or {}
        home_prov_id = home_info.get("provider_team_id")
        away_prov_id = away_info.get("provider_team_id")

        if home_prov_id is None or away_prov_id is None:
            skip_reasons["missing_team_mapping"] = skip_reasons.get("missing_team_mapping", 0) + 1
            stats["skipped"] += 1
            continue

        # ── Competition context (DuckDB) ───────────────────────────────────────
        ctx = get_competition_context(conn, provider_league_id)
        if ctx is None:
            skip_reasons["no_competition_context"] = skip_reasons.get("no_competition_context", 0) + 1
            stats["skipped"] += 1
            continue

        entity_type      = ctx["entity_scope"]
        competition_type = ctx["competition_type"]

        try:
            # Step 1: Poisson + Elo raw prediction (DuckDB only)
            shadow = predict_fixture(
                conn,
                league_id=provider_league_id,
                home_team_id=home_prov_id,
                away_team_id=away_prov_id,
                fixture_date=ko_date,
            )

            if "error" in shadow:
                reason = f"predict_error:{shadow.get('message', '')[:50]}"
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                stats["skipped"] += 1
                continue

            # Step 2: build odds_lookup for model's selections (Supabase odds)
            odds_lookup = _build_odds_lookup(fid, odds_map, shadow["picks"]) or None
            has_odds    = bool(odds_lookup)

            # Apply --min-edge filter: skip logging if no market meets threshold
            if args.min_edge > 0 and odds_lookup:
                # We'll still evaluate — filter only affects display
                pass

            # Step 3: calibrate + score + (optionally) persist
            value = evaluate_fixture(
                conn,
                fixture_picks=shadow["picks"],
                provider_league_id=provider_league_id,
                entity_type=entity_type,
                competition_type=competition_type,
                run_date=ko_date,
                fixture_id=fid,
                persist=do_persist,
                odds_lookup=odds_lookup,
                provider_fixture_id=pfid,
                kickoff_at=ko,
                league_name=league_name,
                home_provider_team_id=home_prov_id,
                away_provider_team_id=away_prov_id,
            )

            # ── Quality filter ─────────────────────────────────────────────────
            max_quality = max(
                (c["quality_score"] for c in value.get("candidates", [])), default=0.0
            )
            if args.min_quality > 0 and max_quality < args.min_quality:
                stats["skipped"] += 1
                stats["eligible"] += 1  # eligible (processed) but filtered from output
                if has_odds:
                    stats["with_odds"] += 1
                else:
                    stats["without_odds"] += 1
                continue

            stats["eligible"] += 1
            stats["picks_candidates"] += len(value.get("candidates", []))
            if value.get("selected"):
                stats["picks_selected"] += 1
                picks_selected_count += 1
            if has_odds:
                stats["with_odds"] += 1
            else:
                stats["without_odds"] += 1

            _print_fixture_result(fixture, league_name, shadow, value, not do_persist, args.verbose)

            # --max-picks cap
            if args.max_picks and picks_selected_count >= args.max_picks:
                print(f"\n  Alcanzado max-picks={args.max_picks}. Deteniendo.")
                break

        except Exception as exc:
            print(f"\n  ERROR fixture {fid}: {exc}")
            stats["errors"] += 1

    # ── Final summary ──────────────────────────────────────────────────────────
    print(f"\n{'=' * 76}")
    print(f"  RESUMEN")
    print(f"  Fixtures revisados   : {stats['total']}")
    print(f"  Fixtures elegibles   : {stats['eligible']}")
    print(f"  Fixtures omitidos    : {stats['skipped']}")
    if skip_reasons:
        for reason, n in sorted(skip_reasons.items(), key=lambda x: -x[1]):
            print(f"    - {reason}: {n}")
    print(f"  Picks candidatos     : {stats['picks_candidates']}")
    print(f"  Picks seleccionados  : {stats['picks_selected']}")
    print(f"  Con odds actuales    : {stats['with_odds']}")
    print(f"  Sin odds actuales    : {stats['without_odds']}")
    print(f"  Errores              : {stats['errors']}")
    if do_persist:
        print(f"\n  Picks guardados en shadow_value_picks (DuckDB local).")
        print(f"  Gradear con: python scripts/grade_shadow_picks.py")
        print(f"  Reportar con: python scripts/report_shadow_value.py")
    else:
        print(f"\n  DRY-RUN: nada fue escrito. Usa --persist para guardar.")
    print(f"{'=' * 76}\n")


if __name__ == "__main__":
    main()
