#!/usr/bin/env python
"""Diagnóstico read-only de cobertura del pipeline de sync.

Muestra qué ligas están en .env, cuáles en Supabase (competition_seasons),
cuáles rastreadas (tracked_competitions), y cuántos fixtures/odds/candidatos
hay por liga en un rango de fechas.

Usage:
    python scripts/audit_sync_coverage.py
    python scripts/audit_sync_coverage.py --days 3
    python scripts/audit_sync_coverage.py --date 2026-04-28 --days 2
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
    from app.core.config import settings
    from app.core.logger import setup_logger
    from app.data.repositories.supabase_client import get_supabase
except ImportError as exc:
    print(f"Error de importación: {exc}")
    sys.exit(2)

SEP = "=" * 80
SEP2 = "-" * 80


def _yn(v: bool) -> str:
    return "SI" if v else "NO"


def _pct(n: int, total: int) -> str:
    if total == 0:
        return "0%"
    return f"{n * 100 // total}%"


# ── Supabase queries ──────────────────────────────────────────────────────────


def _get_competition_seasons_for_leagues(league_ids: list[int]) -> dict[int, dict]:
    """Return {provider_league_id: {cs_id, season, current, name, country}} for given leagues."""
    if not league_ids:
        return {}
    client = get_supabase()
    # Batch in chunks of 200 to avoid URL length limits
    result_map: dict[int, dict] = {}
    for i in range(0, len(league_ids), 200):
        chunk = league_ids[i:i+200]
        resp = (
            client.table("competition_seasons")
            .select("id, season, current, competitions(id, provider_league_id, name, country)")
            .in_("competitions.provider_league_id", chunk)
            .execute()
        )
        for row in (resp.data or []):
            comp = row.get("competitions") or {}
            if not isinstance(comp, dict):
                continue
            lid = comp.get("provider_league_id")
            if lid is None:
                continue
            # Keep the row with highest season if duplicates
            existing = result_map.get(lid)
            if existing is None or row["season"] > existing["season"]:
                result_map[lid] = {
                    "cs_id":   row["id"],
                    "season":  row["season"],
                    "current": row.get("current", False),
                    "name":    comp.get("name") or "?",
                    "country": comp.get("country") or "",
                }
    return result_map


def _get_tracked_leagues() -> dict[int, dict]:
    """Return {provider_league_id: {cs_id, season, tier, is_active}} from tracked_competitions."""
    client = get_supabase()
    resp = (
        client.table("tracked_competitions")
        .select(
            "competition_season_id, sync_tier, is_active, priority, "
            "competition_seasons(season, competitions(provider_league_id, name))"
        )
        .execute()
    )
    out: dict[int, dict] = {}
    for row in (resp.data or []):
        cs = row.get("competition_seasons") or {}
        if not isinstance(cs, dict):
            continue
        comp = cs.get("competitions") or {}
        if not isinstance(comp, dict):
            continue
        lid = comp.get("provider_league_id")
        if lid is None:
            continue
        out[lid] = {
            "cs_id":     row.get("competition_season_id"),
            "season":    cs.get("season"),
            "tier":      row.get("sync_tier") or "tier_1_daily",
            "is_active": row.get("is_active", False),
            "priority":  row.get("priority") or 5,
            "name":      comp.get("name") or "?",
        }
    return out


def _get_fixture_counts_for_range(
    cs_ids: list[int], start_utc: str, end_utc: str
) -> dict[int, int]:
    """Return {competition_season_id: fixture_count} for date range."""
    if not cs_ids:
        return {}
    client = get_supabase()
    counts: dict[int, int] = {}
    chunk_size = 50
    for i in range(0, len(cs_ids), chunk_size):
        chunk = cs_ids[i:i+chunk_size]
        resp = (
            client.table("fixtures")
            .select("league_id", count="exact")
            .in_("league_id", chunk)
            .gte("kickoff_at", start_utc)
            .lt("kickoff_at", end_utc)
            .execute()
        )
        for row in (resp.data or []):
            lid = row.get("league_id")
            if lid:
                counts[lid] = counts.get(lid, 0) + 1
    return counts


def _get_odds_fixture_counts(
    cs_ids: list[int], start_utc: str, end_utc: str
) -> dict[int, int]:
    """Return {competition_season_id: fixtures_with_odds_count}."""
    if not cs_ids:
        return {}
    client = get_supabase()
    # Get all fixture IDs in range
    fix_resp = (
        client.table("fixtures")
        .select("id, league_id")
        .in_("league_id", cs_ids)
        .gte("kickoff_at", start_utc)
        .lt("kickoff_at", end_utc)
        .execute()
    )
    fix_rows = fix_resp.data or []
    if not fix_rows:
        return {}

    fix_ids = [r["id"] for r in fix_rows]
    cs_by_fix = {r["id"]: r["league_id"] for r in fix_rows}

    # Get fixtures that have at least one odds row
    odds_resp = (
        client.table("odds_snapshots")
        .select("fixture_id")
        .in_("fixture_id", fix_ids)
        .limit(5000)
        .execute()
    )
    seen_fixtures: set[int] = set()
    counts: dict[int, int] = {}
    for row in (odds_resp.data or []):
        fid = row.get("fixture_id")
        if fid and fid not in seen_fixtures:
            seen_fixtures.add(fid)
            cs_id = cs_by_fix.get(fid)
            if cs_id:
                counts[cs_id] = counts.get(cs_id, 0) + 1
    return counts


def _get_candidates_fixture_counts(
    cs_ids: list[int], start_utc: str, end_utc: str
) -> dict[int, int]:
    """Return {competition_season_id: pick_candidates_count}."""
    if not cs_ids:
        return {}
    client = get_supabase()
    fix_resp = (
        client.table("fixtures")
        .select("id, league_id")
        .in_("league_id", cs_ids)
        .gte("kickoff_at", start_utc)
        .lt("kickoff_at", end_utc)
        .execute()
    )
    fix_rows = fix_resp.data or []
    if not fix_rows:
        return {}

    fix_ids = [r["id"] for r in fix_rows]
    cs_by_fix = {r["id"]: r["league_id"] for r in fix_rows}

    cand_resp = (
        client.table("pick_candidates")
        .select("fixture_id")
        .in_("fixture_id", fix_ids)
        .limit(10000)
        .execute()
    )
    counts: dict[int, int] = {}
    for row in (cand_resp.data or []):
        fid = row.get("fixture_id")
        if fid:
            cs_id = cs_by_fix.get(fid)
            if cs_id:
                counts[cs_id] = counts.get(cs_id, 0) + 1
    return counts


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    p = argparse.ArgumentParser(
        description="Diagnóstico read-only de cobertura sync.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                   help="Fecha base (default: hoy local).")
    p.add_argument("--days", type=int, default=1,
                   help="Días hacia adelante (default: 1).")
    args = p.parse_args()

    setup_logger()
    logging.getLogger("app").setLevel(logging.WARNING)

    tz = ZoneInfo(settings.default_timezone)
    if args.date:
        base = datetime.fromisoformat(args.date).replace(tzinfo=tz)
    else:
        base = datetime.combine(datetime.now(tz).date(), time.min).replace(tzinfo=tz)

    end_dt    = base + timedelta(days=args.days)
    start_utc = base.astimezone(ZoneInfo("UTC")).isoformat()
    end_utc   = end_dt.astimezone(ZoneInfo("UTC")).isoformat()

    print(f"\n{SEP}")
    print(f"  AUDIT SYNC COVERAGE")
    print(f"  Rango: {start_utc[:10]} -> {end_utc[:10]}  ({args.days} día(s))")
    print(f"{SEP}")

    # ── 1. Settings ────────────────────────────────────────────────────────────
    env_ids   = settings.league_ids_list
    env_seas  = settings.league_seasons_map
    print(f"\n  CONFIGURACIÓN .env")
    print(f"  DEFAULT_LEAGUE_IDS  : {len(env_ids)} ligas")
    print(f"  LEAGUE_SEASONS      : {len(env_seas)} mapeos explícitos")
    if len(env_ids) != len(env_seas):
        missing_seasons = set(env_ids) - set(env_seas.keys())
        if missing_seasons:
            print(f"  ADVERTENCIA: {len(missing_seasons)} ligas sin LEAGUE_SEASONS"
                  f" (usarán DEFAULT_SEASON={settings.default_season}): "
                  f"{sorted(missing_seasons)}")

    if not env_ids:
        print("\n  ERROR: DEFAULT_LEAGUE_IDS vacío. Agrega ligas al .env.")
        sys.exit(1)

    # ── 2. Supabase: competition_seasons ───────────────────────────────────────
    print(f"\n  Consultando Supabase...")
    try:
        cs_map      = _get_competition_seasons_for_leagues(env_ids)
        tracked_map = _get_tracked_leagues()
    except Exception as exc:
        print(f"  ERROR conectando Supabase: {exc}")
        sys.exit(1)

    # Build cs_id -> provider_league_id reverse map for tracked
    cs_id_to_lid = {v["cs_id"]: k for k, v in cs_map.items() if v.get("cs_id")}

    # Determine which cs_ids to query for fixtures/odds/candidates
    # Only cs_ids that correspond to ACTIVE tracked leagues
    active_cs_ids = [
        info["cs_id"] for lid, info in cs_map.items()
        if tracked_map.get(lid, {}).get("is_active")
        and info.get("cs_id")
    ]

    # Also include any tracked leagues cs_ids
    tracked_cs_ids = [info["cs_id"] for info in tracked_map.values() if info.get("cs_id")]
    all_cs_ids = list(set(active_cs_ids) | set(tracked_cs_ids))

    try:
        fix_counts  = _get_fixture_counts_for_range(all_cs_ids, start_utc, end_utc)
        odds_counts = _get_odds_fixture_counts(all_cs_ids, start_utc, end_utc)
        cand_counts = _get_candidates_fixture_counts(all_cs_ids, start_utc, end_utc)
    except Exception as exc:
        print(f"  ADVERTENCIA: error consultando fixtures/odds/candidatos: {exc}")
        fix_counts  = {}
        odds_counts = {}
        cand_counts = {}

    # ── 3. Summary ─────────────────────────────────────────────────────────────
    in_db       = {lid for lid in env_ids if lid in cs_map}
    not_in_db   = set(env_ids) - in_db
    tracked_active = {lid for lid, info in tracked_map.items() if info.get("is_active")}
    env_tracked    = {lid for lid in env_ids if lid in tracked_active}
    env_not_tracked= {lid for lid in env_ids if lid not in tracked_active}

    print(f"\n  RESUMEN")
    print(f"  Ligas en .env                    : {len(env_ids)}")
    print(f"  Con competition_season en BD      : {len(in_db)}  "
          f"({_pct(len(in_db), len(env_ids))})")
    print(f"  Sin competition_season (Phase B)  : {len(not_in_db)}")
    print(f"  Rastreadas y activas (sync diario): {len(env_tracked)}  "
          f"({_pct(len(env_tracked), len(env_ids))})")
    print(f"  No rastreadas (no estarán en sync): {len(env_not_tracked)}")
    print(f"  Total tracked_competitions activas: {len(tracked_active)}")

    # ── 4. Per-league table ────────────────────────────────────────────────────
    print(f"\n  DETALLE POR LIGA  (rango {start_utc[:10]} -> {end_utc[:10]})")
    hdr = (f"  {'lid':>5}  {'Nombre':20}  {'Season':6}  "
           f"{'InDB':4}  {'Track':5}  {'Tier':18}  "
           f"{'Fix':4}  {'Odds':4}  {'Cands':5}  Status")
    print(f"\n{hdr}")
    print(f"  {'-'*76}")

    rows: list[tuple] = []
    for lid in sorted(env_ids):
        season    = env_seas.get(lid, settings.default_season)
        cs_info   = cs_map.get(lid) or {}
        trk_info  = tracked_map.get(lid) or {}
        in_db_ok  = bool(cs_info)
        tracked_ok= trk_info.get("is_active", False)
        tier      = trk_info.get("tier", "---") if tracked_ok else "---"
        name      = cs_info.get("name") or trk_info.get("name") or "?"
        cs_id     = cs_info.get("cs_id") or trk_info.get("cs_id")
        n_fix     = fix_counts.get(cs_id, 0) if cs_id else 0
        n_odds    = odds_counts.get(cs_id, 0) if cs_id else 0
        n_cands   = cand_counts.get(cs_id, 0) if cs_id else 0

        if tracked_ok:
            if n_fix > 0:
                status = "ACTIVA+FIXTURES"
            else:
                status = "ACTIVA_sin_partidos"
        elif in_db_ok:
            status = "SYNC_NECESARIO"
        else:
            status = "PHASE_B_PRIMERO"

        rows.append((lid, name, season, in_db_ok, tracked_ok, tier, n_fix, n_odds, n_cands, status))

    for lid, name, season, in_db_ok, tracked_ok, tier, n_fix, n_odds, n_cands, status in rows:
        mark_db  = "SI" if in_db_ok else "NO"
        mark_trk = "SI" if tracked_ok else "NO"
        print(
            f"  {lid:>5}  {name[:20]:20}  {season:>6}  "
            f"{mark_db:>4}  {mark_trk:>5}  {tier[:18]:18}  "
            f"{n_fix:>4}  {n_odds:>4}  {n_cands:>5}  {status}"
        )

    # ── 5. Ligas tracked activas que NO están en el .env ──────────────────────
    extra_tracked = {lid for lid in tracked_active if lid not in set(env_ids)}
    if extra_tracked:
        print(f"\n  LIGAS EN tracked_competitions PERO NO EN .env ({len(extra_tracked)}):")
        for lid in sorted(extra_tracked):
            info = tracked_map[lid]
            print(f"    liga={lid:>5}  {info['name'][:30]:30}  tier={info['tier']}  "
                  f"season={info['season']}")

    # ── 6. Bloqueadas por Phase B ──────────────────────────────────────────────
    if not_in_db:
        print(f"\n  LIGAS SIN competition_season (requieren Phase B): {sorted(not_in_db)}")
        print(f"  -> Ejecuta: python scripts/sync_reference.py --phase b")

    # ── 7. Ligas en BD pero no rastreadas ─────────────────────────────────────
    not_tracked_but_in_db = {lid for lid in env_ids if lid in in_db and lid not in tracked_active}
    if not_tracked_but_in_db:
        print(f"\n  LIGAS EN BD PERO NO EN tracked_competitions ({len(not_tracked_but_in_db)}):")
        print(f"  IDs: {sorted(not_tracked_but_in_db)}")
        print(f"  -> Ejecuta: python scripts/sync_tracked_competitions_from_env.py")

    # ── 8. Ligues activas con fixtures en el rango ─────────────────────────────
    total_fix   = sum(fix_counts.get(v["cs_id"], 0)
                      for v in cs_map.values() if v.get("cs_id"))
    total_odds  = sum(odds_counts.get(v["cs_id"], 0)
                      for v in cs_map.values() if v.get("cs_id"))
    total_cands = sum(cand_counts.get(v["cs_id"], 0)
                      for v in cs_map.values() if v.get("cs_id"))

    print(f"\n  TOTALES EN RANGO")
    print(f"  Fixtures encontrados : {total_fix}")
    print(f"  Con odds             : {total_odds}")
    print(f"  Pick candidates      : {total_cands}")

    # ── 9. Veredicto ──────────────────────────────────────────────────────────
    print(f"\n  VEREDICTO")
    if not_tracked_but_in_db:
        print(f"  NO {len(not_tracked_but_in_db)} ligas en BD pero no rastreadas — "
              f"sync_today.py las ignorará.")
        print(f"    -> Ejecuta sync_tracked_competitions_from_env.py para agregarlas.")
    if not_in_db:
        print(f"  NO {len(not_in_db)} ligas sin Phase B — sync_today.py no puede procesarlas.")
        print(f"    -> Ejecuta sync_reference.py --phase b")
    if not not_tracked_but_in_db and not not_in_db:
        print(f"  OK Todas las ligas del .env están en tracked_competitions.")
        print(f"  OK sync_today.py procesará {len(env_tracked)} ligas.")

    print(f"\n{SEP}\n")


if __name__ == "__main__":
    main()
