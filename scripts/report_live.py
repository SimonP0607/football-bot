#!/usr/bin/env python
"""Report live monitoring state and settlement results (Phase 7). Read-only.

Shows current live fixture states, pick tracking, and recent settlements.

Usage:
    python scripts/report_live.py              # today's live + recent settlements
    python scripts/report_live.py --fixture 1060362
    python scripts/report_live.py --settled    # only settled picks
    python scripts/report_live.py --pending    # only pending/live picks
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

_STATE_ICON  = {"winning": "W", "losing": "L", "open": "?"}
_STATUS_LABEL = {
    "1H": "1T", "HT": "ET", "2H": "2T", "ET": "Pról",
    "FT": "FT", "AET": "FT(ET)", "PEN": "FT(P)",
    "NS": "No iniciado", "PST": "Aplazado",
}
_MKT_LABEL = {"1X2": "1X2", "OU25": "O/U2.5", "BTTS": "BTTS", "DC": "DC"}


def cmd_fixture(args: argparse.Namespace, conn) -> None:
    from app.services.live_monitor_service import get_fixture_live_state

    state = get_fixture_live_state(conn, args.fixture)
    print()
    print(f"  === Fixture {args.fixture} ===")
    if not state:
        print("  Sin datos live. Ejecuta: python scripts/live_monitor.py --execute")
        return
    _print_fixture_state(state)


def cmd_today(args: argparse.Namespace, conn) -> None:
    from app.services.live_monitor_service import get_live_summary
    from app.data.repositories import settlement_repo

    summary  = get_live_summary(conn)
    now = datetime.now(timezone.utc)

    if not args.settled:
        print()
        print(f"  === LIVE ({len(summary)} fixtures rastreados) ===")
        if not summary:
            print("  Sin fixtures rastreados. Ejecuta:")
            print("  python scripts/live_monitor.py --execute")
        for snap in summary:
            _print_fixture_state(snap)

    if not args.pending:
        # Recent settlements (last 24h)
        since = now - timedelta(hours=24)
        try:
            rows = settlement_repo.get_settled(days=1)
            print()
            print(f"  === LIQUIDADOS (últimas 24h) — {len(rows)} picks ===")
            if not rows:
                print("  Sin liquidaciones recientes.")
            for r in rows[:10]:
                icon     = {"win": "W", "loss": "L", "void": "-"}.get(r["result_status"], "?")
                market   = _MKT_LABEL.get(r.get("market_key", "?"), r.get("market_key", "?"))
                sel      = r.get("selection", "?")
                profit   = float(r.get("profit_units") or 0)
                settled  = (r.get("settled_at") or "")[:16].replace("T", " ")
                print(
                    f"  [{icon}] {market}/{sel}  "
                    f"profit={profit:+.4f}u  [{settled}]"
                )
        except Exception as exc:
            print(f"  Error cargando liquidaciones: {exc}")


def _print_fixture_state(snap: dict) -> None:
    pfid    = snap.get("provider_fixture_id", "?")
    status  = snap.get("status_short", "?")
    elapsed = snap.get("status_elapsed")
    gh      = snap.get("goals_home", 0) or 0
    ga      = snap.get("goals_away", 0) or 0
    picks   = snap.get("picks", [])
    is_fin  = snap.get("is_finished", False)
    last    = (snap.get("last_seen_at") or "")[:16].replace("T", " ")

    status_label = _STATUS_LABEL.get(status, status)
    elapsed_str  = f" {elapsed}'" if elapsed and not is_fin else ""

    print()
    print(f"  fixture={pfid}  [{status_label}{elapsed_str}]  {gh}-{ga}")
    if last:
        print(f"  ultimo: {last} UTC")
    for pk in picks:
        icon  = _STATE_ICON.get(pk.get("live_state", "open"), "?")
        mkt   = _MKT_LABEL.get(pk.get("market_key", "?"), pk.get("market_key", "?"))
        sel   = pk.get("selection", "?")
        state = (pk.get("live_state") or "open").upper()
        print(f"    [{icon}] {mkt}/{sel}  {state}")


def main(args: argparse.Namespace) -> None:
    from app.data.local.duckdb_client import get_local_db, init_schema

    conn = get_local_db()
    init_schema(conn)

    print()
    print("=" * 60)
    print("  REPORT LIVE MONITOR (Phase 7)")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
    print("=" * 60)

    if args.fixture:
        cmd_fixture(args, conn)
    else:
        cmd_today(args, conn)

    print()
    print("=" * 60)
    print()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reporte live monitor (Phase 7, read-only)")
    p.add_argument("--fixture", type=int, default=None, metavar="ID",
                   help="Reporte para un fixture específico")
    p.add_argument("--settled", action="store_true",
                   help="Solo mostrar liquidaciones recientes")
    p.add_argument("--pending", action="store_true",
                   help="Solo mostrar picks live/pendientes")
    return p.parse_args()


if __name__ == "__main__":
    main(parse_args())
