#!/usr/bin/env python
"""Report player availability for a specific fixture or team.

Usage:
    python scripts/report_availability.py --fixture 1060362
    python scripts/report_availability.py --team 33
    python scripts/report_availability.py --fixture 1060362 --json
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: F401


_IMPACT_LABELS = {
    "none":    "✅ Sin bajas",
    "low":     "ℹ️  Impacto bajo",
    "medium":  "📊 Impacto medio",
    "high":    "⚠️  Impacto ALTO",
    "unknown": "❓ Sin datos confirmados",
}

_COVERAGE_LABELS = {
    "data":      "Datos confirmados",
    "no_data":   "Sin datos (API sin cobertura o sin bajas)",
    "api_error": "Error de API",
    "unknown":   "Sin sync",
}


def _open_conn():
    from app.data.local.duckdb_client import get_local_db, init_schema
    conn = get_local_db()
    init_schema(conn)
    return conn


def cmd_fixture(args: argparse.Namespace) -> None:
    conn = _open_conn()
    provider_fixture_id = args.fixture

    from app.data.local.availability_repo import (
        get_injuries_for_fixture, get_lineups_for_fixture,
        get_team_summaries_for_fixture, get_signals_for_fixture,
    )

    summaries = get_team_summaries_for_fixture(conn, provider_fixture_id)
    injuries = get_injuries_for_fixture(conn, provider_fixture_id)
    lineups = get_lineups_for_fixture(conn, provider_fixture_id)
    signals = get_signals_for_fixture(conn, provider_fixture_id)

    if args.json:
        out = {
            "provider_fixture_id": provider_fixture_id,
            "summaries": summaries,
            "injuries": injuries,
            "lineups": lineups,
            "signals": signals,
        }
        print(json.dumps(out, indent=2, default=str))
        return

    print()
    print(f"  Disponibilidad — Fixture {provider_fixture_id}")
    print("=" * 60)

    if not summaries:
        print()
        print("  Sin datos de disponibilidad para este fixture.")
        print("  Ejecuta:")
        print(f"    python scripts/sync_availability_today.py --execute --fixture {provider_fixture_id}")
        print()
        return

    # Group injuries by team
    inj_by_team: dict[int, list[dict]] = {}
    for inj in injuries:
        inj_by_team.setdefault(inj["team_id"], []).append(inj)

    sig_by_team: dict[int, list[dict]] = {}
    for sig in signals:
        sig_by_team.setdefault(sig["team_id"], []).append(sig)

    for team_id, summary in sorted(summaries.items()):
        print()
        print(f"  Equipo {team_id}")
        print(f"  Cobertura:  {_COVERAGE_LABELS.get(summary.get('coverage_status', ''), '?')}")
        print(f"  Impacto:    {_IMPACT_LABELS.get(summary.get('impact_label', ''), '?')}")

        team_injuries = inj_by_team.get(team_id, [])
        if team_injuries:
            print(f"\n  Bajas ({len(team_injuries)}):")
            for inj in team_injuries:
                itype = inj.get("type") or "injured"
                reason = inj.get("reason") or ""
                pid = f"#{inj['player_id']}" if inj.get("player_id") else ""
                reason_str = f" — {reason}" if reason else ""
                print(f"    {inj['player_name']:<28} {pid:<8} [{itype}]{reason_str}")
        elif summary.get("coverage_status") == "data":
            print("  Bajas: ninguna confirmada ✅")

        team_sigs = sig_by_team.get(team_id, [])
        if team_sigs:
            print(f"\n  Señales de disponibilidad ({len(team_sigs)}):")
            for sig in team_sigs:
                pos = sig.get("position") or "pos.desconocida"
                sev = sig.get("severity") or "?"
                conf = sig.get("confidence") or "?"
                print(
                    f"    {sig['player_name']:<28} tipo={sig['signal_type']}"
                    f"  sev={sev}  conf={conf}  pos={pos}"
                )

        lu = lineups.get(team_id)
        if lu:
            formation = lu.get("formation") or "?"
            coach = lu.get("coach_name") or "?"
            print(f"\n  Alineación confirmada: {formation}  Coach: {coach}")

    print()


def cmd_team(args: argparse.Namespace) -> None:
    conn = _open_conn()
    from app.data.local.availability_repo import get_injuries_for_team

    injuries = get_injuries_for_team(conn, args.team, limit=30)

    if args.json:
        print(json.dumps(injuries, indent=2, default=str))
        return

    print()
    print(f"  Bajas recientes — Equipo {args.team}")
    print("=" * 60)

    if not injuries:
        print()
        print("  Sin bajas registradas para este equipo.")
        print("  Ejecuta sync_availability_today.py para cargar datos.")
        print()
        return

    print()
    print(f"  {'Fixture':<12} {'Jugador':<28} {'Tipo':<12} {'Razón'}")
    print("  " + "-" * 60)
    for inj in injuries:
        reason = (inj.get("reason") or "")[:25]
        print(
            f"  {inj['provider_fixture_id']:<12} {inj['player_name']:<28} "
            f"{(inj.get('type') or '?'):<12} {reason}"
        )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reporte de disponibilidad de jugadores"
    )
    parser.add_argument("--fixture", type=int, metavar="ID",
                        help="Reporte por provider_fixture_id")
    parser.add_argument("--team", type=int, metavar="ID",
                        help="Bajas recientes por provider_team_id")
    parser.add_argument("--json", action="store_true", help="Salida JSON")
    args = parser.parse_args()

    if not any([args.fixture, args.team]):
        parser.print_help()
        sys.exit(0)

    if args.fixture:
        cmd_fixture(args)
    elif args.team:
        cmd_team(args)


if __name__ == "__main__":
    main()
