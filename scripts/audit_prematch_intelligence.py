#!/usr/bin/env python
"""Audit prematch intelligence state (Phase 6). Read-only.

Shows fixtures with prematch intelligence, odds drift summary, picks affected,
lineup coverage, and alert distribution.

Usage:
    python scripts/audit_prematch_intelligence.py
    python scripts/audit_prematch_intelligence.py --hours 12 --league 39
"""

from __future__ import annotations

import argparse
import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: F401


def _yn(v: bool) -> str:
    return "SI" if v else "NO"


def _pct(n: int, total: int) -> str:
    if total == 0:
        return "--"
    return f"{n}/{total} ({n * 100 // total}%)"


def main(args: argparse.Namespace) -> None:
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.data.local.prematch_repo import prematch_counts, get_recent_alerts

    conn = get_local_db()
    init_schema(conn)

    now = datetime.now(timezone.utc)
    print()
    print("=" * 72)
    print("  AUDIT PREMATCH INTELLIGENCE (Phase 6)")
    print(f"  {now.strftime('%Y-%m-%d %H:%M')} UTC")
    print("=" * 72)

    # Config
    print(f"\n  CONFIG")
    print(f"  PREMATCH_INTELLIGENCE_ENABLED  : {_yn(settings.prematch_intelligence_enabled)}")
    print(f"  PREMATCH_REFRESH_HOURS         : {settings.prematch_refresh_hours}")
    print(f"  PREMATCH_LINEUPS_WINDOW_MINUTES: {settings.prematch_lineups_window_minutes}")
    print(f"  PREMATCH_MAX_REQUESTS          : {settings.prematch_max_requests}")
    print(f"  PREMATCH_PENALTY_HIGH_DRIFT    : {settings.prematch_penalty_high_drift}")
    print(f"  PREMATCH_PENALTY_MEDIUM_DRIFT  : {settings.prematch_penalty_medium_drift}")
    print(f"  PREMATCH_BOOST_SUPPORTING_MOVE : {settings.prematch_boost_supporting_move}")
    print(f"  PREMATCH_REJECT_HIGH_RISK      : {_yn(settings.prematch_reject_high_risk)}")

    # Table counts
    counts = prematch_counts(conn)
    print(f"\n  TABLAS DUCKDB")
    for table, cnt in counts.items():
        print(f"  {table:<36}: {cnt:>6}")

    if all(v <= 0 for v in counts.values()):
        print()
        print("  Sin datos prematch en DuckDB.")
        print("  Ejecuta: python scripts/sync_prematch_intelligence.py --execute")
        print()
        print("=" * 72)
        return

    # Odds movement summary
    print(f"\n  MOVIMIENTO DE CUOTAS")
    try:
        rows = conn.execute(
            """
            SELECT movement_direction, movement_strength, COUNT(*) as cnt
            FROM prematch_odds_movement
            GROUP BY movement_direction, movement_strength
            ORDER BY movement_direction, movement_strength
            """
        ).fetchall()
        if rows:
            for direction, strength, cnt in rows:
                print(f"  {direction or 'unknown':12} / {strength or 'none':6}: {cnt:>5} rows")
        else:
            print("  Sin datos de movimiento")
    except Exception as exc:
        print(f"  Error: {exc}")

    # High/medium drift rows
    print(f"\n  DRIFT FUERTE (medium+high drifting)")
    try:
        drift_rows = conn.execute(
            """
            SELECT provider_fixture_id, market_key, selection,
                   opening_odds, current_odds, odds_delta, implied_delta,
                   movement_strength, captured_at
            FROM prematch_odds_movement
            WHERE movement_direction = 'drifting'
              AND movement_strength IN ('medium', 'high')
            ORDER BY ABS(implied_delta) DESC
            LIMIT 15
            """
        ).fetchall()
        if drift_rows:
            print(f"  {'fixture':>10}  {'mkt':>6}  {'sel':>5}  {'open':>6}  {'curr':>6}  "
                  f"{'delta':>7}  {'impl_d':>8}  {'str':>6}")
            print(f"  {'-' * 68}")
            for r in drift_rows:
                print(
                    f"  {r[0]:>10}  {r[1]:>6}  {r[2]:>5}  "
                    f"{r[3]:>6.2f}  {r[4]:>6.2f}  "
                    f"{r[5]:>+7.3f}  {r[6]:>+8.4f}  {r[7]:>6}"
                )
        else:
            print("  Sin drift fuerte registrado")
    except Exception as exc:
        print(f"  Error: {exc}")

    # Supporting moves
    print(f"\n  MOVIMIENTO A FAVOR (shortening)")
    try:
        support_rows = conn.execute(
            """
            SELECT provider_fixture_id, market_key, selection,
                   opening_odds, current_odds, movement_strength
            FROM prematch_odds_movement
            WHERE movement_direction = 'shortening'
              AND movement_strength != 'none'
            ORDER BY ABS(implied_delta) DESC
            LIMIT 10
            """
        ).fetchall()
        if support_rows:
            for r in support_rows:
                print(
                    f"  fixture={r[0]} {r[1]}/{r[2]}  "
                    f"{r[3]:.2f} -> {r[4]:.2f}  [{r[5]}]"
                )
        else:
            print("  Sin movimientos de apoyo registrados")
    except Exception as exc:
        print(f"  Error: {exc}")

    # Published picks with drift against them
    print(f"\n  PICKS OFICIALES CON DRIFT EN CONTRA")
    try:
        from app.data.repositories.supabase_client import get_supabase
        client  = get_supabase()
        start_utc = now.isoformat()
        end_utc   = (now + timedelta(hours=args.hours)).isoformat()
        resp = (
            client.table("fixtures")
            .select("id, provider_fixture_id, kickoff_at")
            .gte("kickoff_at", start_utc)
            .lt("kickoff_at", end_utc)
            .limit(200)
            .execute()
        )
        upcoming_fixes = {f["id"]: f for f in (resp.data or [])}

        if upcoming_fixes:
            picks_resp = (
                client.table("pick_candidates")
                .select("fixture_id, market_key, selection, is_publishable")
                .in_("fixture_id", list(upcoming_fixes.keys()))
                .eq("is_publishable", True)
                .execute()
            )
            publishable = picks_resp.data or []
            affected = 0
            for pk in publishable:
                pfid = (upcoming_fixes.get(pk["fixture_id"]) or {}).get("provider_fixture_id")
                if not pfid:
                    continue
                has_drift = conn.execute(
                    """
                    SELECT 1 FROM prematch_odds_movement
                    WHERE provider_fixture_id = ?
                      AND market_key = ? AND selection = ?
                      AND movement_direction = 'drifting'
                      AND movement_strength IN ('medium', 'high')
                    LIMIT 1
                    """,
                    [pfid, pk["market_key"], pk["selection"]],
                ).fetchone()
                if has_drift:
                    affected += 1
                    print(
                        f"  fixture={pfid}  {pk['market_key']}/{pk['selection']}  "
                        f"-> drift fuerte detectado"
                    )
            if not affected:
                print("  Ningun pick oficial con drift fuerte")
    except Exception as exc:
        print(f"  Error consultando picks/Supabase: {exc}")

    # Lineups coverage
    print(f"\n  COBERTURA DE ALINEACIONES")
    try:
        lu_rows = conn.execute(
            """
            SELECT lineups_available, lineups_confirmed, COUNT(*) as cnt
            FROM prematch_lineup_status
            GROUP BY lineups_available, lineups_confirmed
            """
        ).fetchall()
        for row in lu_rows:
            avail, conf, cnt = row
            print(
                f"  available={_yn(bool(avail))}  confirmed={_yn(bool(conf))}: {cnt} fixtures"
            )
        if not lu_rows:
            print("  Sin datos de alineaciones")
    except Exception as exc:
        print(f"  Error: {exc}")

    # Alerts
    print(f"\n  ALERTAS (ultimas {args.hours}h)")
    alerts = get_recent_alerts(conn, days=max(1, args.hours // 24 + 1))
    if alerts:
        severity_dist: dict[str, int] = {}
        type_dist:     dict[str, int] = {}
        for a in alerts:
            sev = a.get("severity", "low")
            atype = a.get("alert_type", "?")
            severity_dist[sev] = severity_dist.get(sev, 0) + 1
            type_dist[atype]   = type_dist.get(atype, 0) + 1
        print(f"  Total alertas: {len(alerts)}")
        for sev in ("high", "medium", "low"):
            if severity_dist.get(sev):
                print(f"    {sev:8}: {severity_dist[sev]}")
        print(f"  Por tipo:")
        for atype, cnt in sorted(type_dist.items(), key=lambda x: -x[1]):
            print(f"    {atype:<36}: {cnt}")
    else:
        print("  Sin alertas recientes")

    print()
    print("=" * 72)
    print()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Audit prematch intelligence (Phase 6, read-only)",
    )
    p.add_argument("--hours", type=int, default=12, metavar="N",
                   help="Ventana de fixtures proximos a revisar (defecto: 12)")
    p.add_argument("--league", type=int, default=None, metavar="ID",
                   help="Filtrar por provider_league_id")
    return p.parse_args()


if __name__ == "__main__":
    main(parse_args())
