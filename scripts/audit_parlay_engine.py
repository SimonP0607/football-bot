"""CLI: Audit the Parlay Engine — picks, generation, risk, settlement.

Usage:
  python scripts/audit_parlay_engine.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()


def _section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def main() -> None:
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.data.local import parlay_repo as repo
    from app.core.config import settings

    conn = get_local_db()
    init_schema(conn)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"\nParlay Engine Audit — {today}")

    # ── Configuration ─────────────────────────────────────────────────────────
    _section("1. Configuración")
    print(f"  parlay_engine_enabled:    {settings.parlay_engine_enabled}")
    print(f"  parlay_max_legs:          {settings.parlay_max_legs}")
    print(f"  parlay_min_edge:          {settings.parlay_min_edge}")
    print(f"  parlay_min_ev:            {settings.parlay_min_ev}")
    print(f"  parlay_min_confidence:    {settings.parlay_min_confidence}")
    print(f"  parlay_max_correlation:   {settings.parlay_max_correlation}")
    print(f"  parlay_max_risk:          {settings.parlay_max_risk}")
    print(f"  parlay_allow_same_fixture:{settings.parlay_allow_same_fixture}")
    print(f"  parlay_max_same_league:   {settings.parlay_max_same_league}")
    print(f"  parlay_max_per_day:       {settings.parlay_max_per_day}")
    print(f"  parlay_default_stake:     {settings.parlay_default_stake_units}u")

    # ── Table counts ──────────────────────────────────────────────────────────
    _section("2. Tablas DuckDB")
    counts = repo.parlay_counts(conn)
    for table, cnt in counts.items():
        status = "OK" if cnt >= 0 else "ERROR"
        print(f"  [{status}] {table}: {cnt}")

    # ── Picks available today ─────────────────────────────────────────────────
    _section("3. Picks disponibles hoy")
    try:
        from app.services.parlay_engine_service import _load_picks_from_supabase, _filter_eligible
        all_picks = _load_picks_from_supabase(include_observed=False)
        eligible  = _filter_eligible(all_picks)
        excluded  = len(all_picks) - len(eligible)
        print(f"  Picks cargados:   {len(all_picks)}")
        print(f"  Picks elegibles:  {len(eligible)}")
        print(f"  Picks excluidos:  {excluded}")
        if eligible:
            print(f"\n  Elegibles:")
            for p in eligible[:10]:
                pid  = p.get("pick_candidate_id", "?")
                mkt  = p.get("market_key", "?")
                sel  = p.get("selection", "?")
                odds = p.get("odds") or 0
                edge = (p.get("edge") or 0) * 100
                fid  = p.get("fixture_id") or p.get("provider_fixture_id") or "?"
                print(f"    pick {pid}: {mkt}/{sel} @ {odds:.2f}  edge {edge:+.1f}%  fix {fid}")
            if len(eligible) > 10:
                print(f"    ... y {len(eligible) - 10} más")
        if excluded > 0:
            excl_picks = [p for p in all_picks if p not in eligible]
            print(f"\n  Excluidos (muestra):")
            for p in excl_picks[:5]:
                pid  = p.get("pick_candidate_id", "?")
                mkt  = p.get("market_key", "?")
                st   = p.get("recommendation_status", "?")
                edge = (p.get("edge") or 0) * 100
                print(f"    pick {pid}: {mkt}  status={st}  edge={edge:+.1f}%")
    except Exception as exc:
        print(f"  ERROR cargando picks: {exc}")

    # ── Today's parlays ───────────────────────────────────────────────────────
    _section("4. Parlays generados hoy")
    today_all = repo.get_parlay_candidates(conn, date=today, limit=200)
    today_rec = [p for p in today_all if p.get("recommendation_status") == "recommended"]
    today_obs = [p for p in today_all if p.get("recommendation_status") == "observed"]
    today_rej = [p for p in today_all if (p.get("recommendation_status") or "").startswith("rejected")]

    print(f"  Total hoy:        {len(today_all)}")
    print(f"  Recomendados:     {len(today_rec)}")
    print(f"  Observados:       {len(today_obs)}")
    print(f"  Rechazados:       {len(today_rej)}")

    if today_rec:
        print(f"\n  Top recomendados:")
        for p in today_rec[:5]:
            odds = p.get("total_odds") or 0
            ev   = (p.get("ev") or 0) * 100
            risk = (p.get("risk_score") or 0) * 100
            corr = (p.get("correlation_score") or 0) * 100
            ptype = p.get("parlay_type", "?")
            print(f"    {ptype}  cuota {odds:.2f}x  EV {ev:+.1f}%  riesgo {risk:.0f}%  corr {corr:.0f}%")

    if today_rej:
        # Breakdown by rejection reason
        reasons: dict[str, int] = {}
        for p in today_rej:
            r = p.get("rejection_reason") or p.get("recommendation_status") or "unknown"
            reasons[r] = reasons.get(r, 0) + 1
        print(f"\n  Razones de rechazo:")
        for reason, cnt in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"    {reason}: {cnt}")

    # ── Risk distribution ─────────────────────────────────────────────────────
    _section("5. Distribución de riesgo (hoy)")
    if today_all:
        risks = [p.get("risk_score") or 0 for p in today_all]
        corrs = [p.get("correlation_score") or 0 for p in today_all]
        evs   = [p.get("ev") or 0 for p in today_all]
        print(f"  Risk score   — min: {min(risks):.3f}  max: {max(risks):.3f}  avg: {sum(risks)/len(risks):.3f}")
        print(f"  Correlation  — min: {min(corrs):.3f}  max: {max(corrs):.3f}  avg: {sum(corrs)/len(corrs):.3f}")
        print(f"  EV           — min: {min(evs)*100:+.1f}%  max: {max(evs)*100:+.1f}%  avg: {sum(evs)/len(evs)*100:+.1f}%")
    else:
        print("  Sin parlays hoy para analizar.")

    # ── Settlement state ──────────────────────────────────────────────────────
    _section("6. Estado de liquidación")
    results = repo.get_parlay_results(conn)
    settled_keys = {r["parlay_key"] for r in results}
    unsettled = [p for p in repo.get_parlay_candidates(conn, limit=500) if p["parlay_key"] not in settled_keys]
    summary = repo.get_recent_parlay_summary(conn)

    print(f"  Liquidados:       {len(results)}")
    print(f"  Sin liquidar:     {len(unsettled)}")
    print(f"  Performance:")
    print(f"    Wins: {summary['wins']}  Losses: {summary['losses']}  Voids: {summary['voids']}")
    if summary["settled"] > 0:
        print(f"    Profit: {summary['profit_units']:+.4f}u  ROI: {summary['roi_pct']:+.2f}%")

    # ── Inconsistencies ───────────────────────────────────────────────────────
    _section("7. Inconsistencias")
    issues: list[str] = []

    # Legs without candidate
    orphan_legs = conn.execute(
        """
        SELECT COUNT(*) FROM parlay_legs pl
        LEFT JOIN parlay_candidates pc ON pl.parlay_key = pc.parlay_key
        WHERE pc.parlay_key IS NULL
        """
    ).fetchone()
    if orphan_legs and orphan_legs[0] > 0:
        issues.append(f"Legs huérfanas (sin candidato): {orphan_legs[0]}")

    # Results without candidate
    orphan_results = conn.execute(
        """
        SELECT COUNT(*) FROM parlay_results pr
        LEFT JOIN parlay_candidates pc ON pr.parlay_key = pc.parlay_key
        WHERE pc.parlay_key IS NULL
        """
    ).fetchone()
    if orphan_results and orphan_results[0] > 0:
        issues.append(f"Resultados huérfanos (sin candidato): {orphan_results[0]}")

    # Candidates with no legs
    no_legs = conn.execute(
        """
        SELECT COUNT(*) FROM parlay_candidates pc
        LEFT JOIN parlay_legs pl ON pc.parlay_key = pl.parlay_key
        WHERE pl.parlay_key IS NULL
        """
    ).fetchone()
    if no_legs and no_legs[0] > 0:
        issues.append(f"Candidatos sin legs: {no_legs[0]}")

    if issues:
        for issue in issues:
            print(f"  ⚠  {issue}")
    else:
        print("  Sin inconsistencias detectadas.")

    print(f"\n{'='*60}")
    print("  Audit completo.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
