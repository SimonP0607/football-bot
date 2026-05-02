#!/usr/bin/env python
"""Report prematch intelligence for upcoming fixtures (Phase 6). Read-only.

Shows per-fixture summary: kickoff, odds movement, alerts, availability, lineup,
and a risk verdict (OK / observar / riesgo alto).

Usage:
    python scripts/report_prematch.py --fixture 1060362
    python scripts/report_prematch.py --hours 6
    python scripts/report_prematch.py --alerts
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


def _ko_str(kickoff_at: str) -> str:
    if not kickoff_at:
        return "?"
    try:
        ko = datetime.fromisoformat(kickoff_at.replace("Z", "+00:00"))
        ko_local = ko.astimezone()
        return ko_local.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return kickoff_at[:16]


def _risk_verdict(drift_high: bool, drift_med: bool, avail_high: bool) -> str:
    if drift_high or avail_high:
        return "RIESGO ALTO"
    if drift_med:
        return "OBSERVAR"
    return "OK"


def cmd_fixture(args: argparse.Namespace, conn) -> None:
    from app.data.local.prematch_repo import get_prematch_summary_for_fixture
    from app.data.repositories.fixture_repo import get_fixture_by_provider_id

    fix = get_fixture_by_provider_id(args.fixture)
    if not fix:
        print(f"  Fixture {args.fixture} no encontrado en Supabase.")
        return

    summary = get_prematch_summary_for_fixture(conn, args.fixture)
    _print_fixture_report(fix, summary)


def cmd_hours(args: argparse.Namespace, conn) -> None:
    from app.data import supabase_reader
    from app.data.local.prematch_repo import get_prematch_summary_for_fixture

    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=args.hours)
    fixtures = supabase_reader.get_fixtures_for_range(
        now.isoformat(), end.isoformat(), limit=100
    )
    if not fixtures:
        print("  Sin fixtures proximos en Supabase.")
        return

    # Load team names
    team_ids = list({
        t for f in fixtures
        for t in [f.get("home_team_id"), f.get("away_team_id")] if t
    })
    try:
        team_map = supabase_reader.get_team_provider_map(team_ids)
    except Exception:
        team_map = {}

    for fix in fixtures:
        summary = get_prematch_summary_for_fixture(conn, fix.get("provider_fixture_id"))
        _print_fixture_report(fix, summary, team_map=team_map)


def cmd_alerts(conn) -> None:
    from app.data.local.prematch_repo import get_recent_alerts

    alerts = get_recent_alerts(conn, days=1)
    if not alerts:
        print("  Sin alertas en las ultimas 24h.")
        return

    print(f"  ALERTAS RECIENTES ({len(alerts)})")
    print()
    _SEV_ICON = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    for a in alerts:
        icon = _SEV_ICON.get(a.get("severity", "low"), "")
        mkt  = f" [{a['related_market']}/{a['related_selection']}]" \
               if a.get("related_market") else ""
        print(
            f"  {icon} [{a.get('severity', '?').upper():6}] "
            f"fixture={a.get('provider_fixture_id')}  {a.get('alert_type')}{mkt}"
        )
        if a.get("message"):
            print(f"         {a['message'][:80]}")


def _print_fixture_report(
    fix: dict,
    summary: dict | None,
    team_map: dict | None = None,
) -> None:
    prov_fid = fix.get("provider_fixture_id", "?")
    ko       = _ko_str(fix.get("kickoff_at", ""))

    home_id   = fix.get("home_team_id")
    away_id   = fix.get("away_team_id")
    home_name = (team_map or {}).get(home_id, {}).get("name") if team_map else None
    away_name = (team_map or {}).get(away_id, {}).get("name") if team_map else None

    header = f"fixture={prov_fid}  ko={ko}"
    if home_name and away_name:
        header += f"  {home_name} vs {away_name}"

    print()
    print(f"  {'-' * 60}")
    print(f"  {header}")

    if summary is None:
        print("  Sin datos prematch. Ejecuta sync_prematch_intelligence.py")
        return

    odds_mv  = summary.get("odds_movement") or []
    alerts   = summary.get("alerts") or []
    lineup   = summary.get("lineup")

    # Odds movement
    if odds_mv:
        strong = [m for m in odds_mv if m.get("movement_strength") not in ("none", None)]
        if strong:
            print(f"  Cuotas ({len(strong)} con movimiento):")
            for m in strong[:4]:
                d = m.get("movement_direction", "?")
                s = m.get("movement_strength", "?")
                print(
                    f"    {m['market_key']:>6}/{m['selection']:<5}  "
                    f"{m.get('opening_odds', 0):.2f} -> {m.get('current_odds', 0):.2f}"
                    f"  [{d} {s}]"
                )
        else:
            print("  Cuotas: sin movimiento significativo")
    else:
        print("  Cuotas: sin snapshot registrado")

    # Alerts
    if alerts:
        _SEV_ICON = {"high": "🔴", "medium": "🟡", "low": "🟢"}
        print(f"  Alertas ({len(alerts)}):")
        for a in alerts[:4]:
            icon = _SEV_ICON.get(a.get("severity", "low"), "")
            print(f"    {icon} {a.get('title', a.get('alert_type', '?'))}")
    else:
        print("  Alertas: ninguna")

    # Lineup
    if lineup:
        if lineup.get("lineups_confirmed"):
            print(
                f"  Alineaciones: confirmadas  "
                f"({lineup.get('home_formation', '?')} / {lineup.get('away_formation', '?')})"
            )
        elif lineup.get("lineups_available"):
            print("  Alineaciones: disponibles (no confirmadas)")
        else:
            print("  Alineaciones: pendientes")
    else:
        print("  Alineaciones: sin datos")

    # Risk verdict
    drift_high = any(
        m.get("movement_direction") == "drifting" and m.get("movement_strength") == "high"
        for m in odds_mv
    )
    drift_med  = any(
        m.get("movement_direction") == "drifting" and m.get("movement_strength") == "medium"
        for m in odds_mv
    )
    avail_high = (lineup or {}).get("home_impact") == "high" or \
                 (lineup or {}).get("away_impact") == "high"

    verdict = _risk_verdict(drift_high, drift_med, avail_high)
    _VERDICT_ICON = {"OK": "✅", "OBSERVAR": "🟡", "RIESGO ALTO": "🔴"}
    print(f"  Recomendacion: {_VERDICT_ICON.get(verdict, '')} {verdict}")


def main(args: argparse.Namespace) -> None:
    from app.data.local.duckdb_client import get_local_db, init_schema

    conn = get_local_db()
    init_schema(conn)

    print()
    print("=" * 64)
    print("  REPORT PREMATCH INTELLIGENCE (Phase 6)")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
    print("=" * 64)

    if args.alerts:
        cmd_alerts(conn)
    elif args.fixture:
        cmd_fixture(args, conn)
    else:
        cmd_hours(args, conn)

    print()
    print("=" * 64)
    print()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Reporte prematch intelligence (Phase 6, read-only)",
    )
    p.add_argument("--fixture", type=int, default=None, metavar="ID",
                   help="Reporte para un fixture especifico (provider_fixture_id)")
    p.add_argument("--hours", type=int, default=6, metavar="N",
                   help="Reportar fixtures proximos en N horas (defecto: 6)")
    p.add_argument("--alerts", action="store_true",
                   help="Solo mostrar alertas recientes (ultimas 24h)")
    return p.parse_args()


if __name__ == "__main__":
    main(parse_args())
