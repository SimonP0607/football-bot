#!/usr/bin/env python
"""Audit live monitor state (Phase 7). Read-only.

Shows pending picks, live fixture snapshots, pick tracking states,
notification log, and detects inconsistencies.

Usage:
    python scripts/audit_live_monitor.py
    python scripts/audit_live_monitor.py --hours 6
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


def main(args: argparse.Namespace) -> None:
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.data.local.live_monitor_repo import (
        live_monitor_counts,
        get_all_live_snapshots,
        get_all_pick_tracking,
    )
    from app.data.repositories import settlement_repo

    conn = get_local_db()
    init_schema(conn)

    now = datetime.now(timezone.utc)
    print()
    print("=" * 72)
    print("  AUDIT LIVE MONITOR (Phase 7)")
    print(f"  {now.strftime('%Y-%m-%d %H:%M')} UTC")
    print("=" * 72)

    # Config
    print(f"\n  CONFIG")
    print(f"  LIVE_MONITOR_ENABLED          : {_yn(settings.live_monitor_enabled)}")
    print(f"  LIVE_MONITOR_HOURS_BEFORE     : {settings.live_monitor_hours_before}")
    print(f"  LIVE_MONITOR_HOURS_AFTER      : {settings.live_monitor_hours_after}")
    print(f"  LIVE_MONITOR_MAX_REQUESTS     : {settings.live_monitor_max_requests}")
    print(f"  LIVE_MONITOR_INTERVAL_SECONDS : {settings.live_monitor_interval_seconds}")
    print(f"  LIVE_MONITOR_MIN_ELAPSED_NOTIFY: {settings.live_monitor_min_elapsed_notify}")
    print(f"  LIVE_MONITOR_AUTO_SETTLE      : {_yn(settings.live_monitor_auto_settle)}")
    print(f"  LIVE_MONITOR_NOTIFY           : {_yn(settings.live_monitor_notify)}")

    # Table counts
    counts = live_monitor_counts(conn)
    print(f"\n  TABLAS DUCKDB")
    for table, cnt in counts.items():
        print(f"  {table:<36}: {cnt:>6}")

    # Pending picks (Supabase)
    print(f"\n  PICKS PENDIENTES (Supabase)")
    try:
        pending = settlement_repo.get_all_pending_with_fixtures()
        now_ts  = now
        window_start = now_ts - timedelta(hours=args.hours)
        window_end   = now_ts + timedelta(hours=settings.live_monitor_hours_before)

        in_window = []
        for pick in pending:
            fix = pick.get("_fixture", {})
            ko_str = fix.get("kickoff_at")
            if not ko_str:
                continue
            try:
                ko = datetime.fromisoformat(ko_str.replace("Z", "+00:00"))
                if window_start <= ko <= window_end:
                    in_window.append(pick)
            except ValueError:
                pass

        print(f"  Total pendientes     : {len(pending)}")
        print(f"  En ventana live ({args.hours}h) : {len(in_window)}")

        if in_window:
            print(f"\n  {'fixture_id':>12}  {'kickoff':>16}  {'market':>6}  {'selection':>10}  {'tracked?':>8}")
            print(f"  {'-' * 60}")
            snap_ids = {s["provider_fixture_id"] for s in get_all_live_snapshots(conn)}
            track_map = {t["pick_candidate_id"]: t for t in get_all_pick_tracking(conn)}
            for pick in in_window[:20]:
                fix    = pick.get("_fixture", {})
                pfid   = fix.get("provider_fixture_id", "?")
                ko     = (fix.get("kickoff_at") or "")[:16].replace("T", " ")
                mkt    = pick.get("market_key", "?")
                sel    = pick.get("selection", "?")
                pk_id  = pick.get("pick_candidate_id") or pick.get("id")
                tracked = "YES" if pk_id in track_map else "NO"
                print(f"  {pfid:>12}  {ko:>16}  {mkt:>6}  {sel:>10}  {tracked:>8}")
    except Exception as exc:
        print(f"  Error: {exc}")

    # Live snapshots
    snapshots = get_all_live_snapshots(conn)
    print(f"\n  SNAPSHOTS LIVE ({len(snapshots)})")
    _FINISHED = {"FT", "AET", "PEN", "WO"}
    _IN_PLAY  = {"1H", "HT", "2H", "ET", "BT", "P", "SUSP", "INT", "LIVE"}
    if snapshots:
        live_n  = sum(1 for s in snapshots if s.get("status_short") in _IN_PLAY)
        fin_n   = sum(1 for s in snapshots if s.get("is_finished"))
        print(f"  En juego: {live_n}  Finalizados: {fin_n}")
        print()
        print(f"  {'fixture_id':>12}  {'status':>6}  {'elapsed':>7}  {'score':>7}  {'finished?':>9}  {'last_seen':>16}")
        print(f"  {'-' * 68}")
        for s in sorted(snapshots, key=lambda x: x.get("provider_fixture_id", 0)):
            pfid   = s["provider_fixture_id"]
            status = s.get("status_short", "?")
            el     = s.get("status_elapsed")
            el_str = f"{el}'" if el is not None else "?"
            score  = f"{s.get('goals_home', 0) or 0}-{s.get('goals_away', 0) or 0}"
            fin    = "YES" if s.get("is_finished") else "NO"
            seen   = (s.get("last_seen_at") or "")[:16].replace("T", " ")
            print(f"  {pfid:>12}  {status:>6}  {el_str:>7}  {score:>7}  {fin:>9}  {seen:>16}")
    else:
        print("  Sin snapshots live en DuckDB.")
        print("  Ejecuta: python scripts/live_monitor.py --execute")

    # Pick tracking summary
    tracking = get_all_pick_tracking(conn)
    print(f"\n  PICK TRACKING ({len(tracking)})")
    if tracking:
        by_state: dict[str, int] = {}
        for t in tracking:
            s = t.get("live_state", "open")
            by_state[s] = by_state.get(s, 0) + 1
        for state, cnt in sorted(by_state.items(), key=lambda x: -x[1]):
            print(f"  {state:>10}: {cnt}")
    else:
        print("  Sin pick tracking en DuckDB.")

    # Inconsistencies: tracked picks not in pending
    if tracking:
        try:
            pending_ids = {
                (p.get("pick_candidate_id") or p.get("id"))
                for p in settlement_repo.get_all_pending_with_fixtures()
            }
            orphans = [
                t for t in tracking
                if t["pick_candidate_id"] not in pending_ids
            ]
            if orphans:
                print(f"\n  INCONSISTENCIAS — {len(orphans)} picks rastreados ya no pendientes:")
                for t in orphans[:5]:
                    print(
                        f"  pick_id={t['pick_candidate_id']} fixture={t['provider_fixture_id']}"
                        f" {t['market_key']}/{t['selection']} [{t.get('live_state')}]"
                    )
                if len(orphans) > 5:
                    print(f"  ... y {len(orphans) - 5} más")
            else:
                print("\n  Sin inconsistencias detectadas.")
        except Exception as exc:
            print(f"\n  Error verificando consistencia: {exc}")

    # Notification log
    try:
        notif_count = conn.execute("SELECT COUNT(*) FROM live_notifications_log").fetchone()
        print(f"\n  NOTIFICACIONES ENVIADAS: {notif_count[0] if notif_count else 0}")
    except Exception:
        pass

    print()
    print("=" * 72)
    print()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Audit live monitor (Phase 7, read-only)")
    p.add_argument(
        "--hours", type=float, default=None, metavar="H",
        help="Ventana de kickoffs pasados a incluir como pendientes",
    )
    args = p.parse_args()
    if args.hours is None:
        args.hours = settings.live_monitor_hours_after
    return args


if __name__ == "__main__":
    main(parse_args())
