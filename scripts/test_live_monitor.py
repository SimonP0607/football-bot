#!/usr/bin/env python
"""Test suite for Phase 7: Live Monitor (in-memory DuckDB, no network).

All tests use an in-memory DuckDB instance with the schema applied.
No Supabase calls, no API calls, no file I/O.

Usage:
    python scripts/test_live_monitor.py
    python scripts/test_live_monitor.py -v
"""

from __future__ import annotations

import io
import sys
import traceback
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Test harness ───────────────────────────────────────────────────────────────

_passed: list[str] = []
_failed: list[str] = []
_verbose = "-v" in sys.argv


def ok(name: str) -> None:
    _passed.append(name)
    if _verbose:
        print(f"  PASS  {name}")


def fail(name: str, reason: str) -> None:
    _failed.append(name)
    print(f"  FAIL  {name}")
    print(f"        {reason}")


def assert_eq(name: str, got, expected) -> None:
    if got == expected:
        ok(name)
    else:
        fail(name, f"got {got!r}, expected {expected!r}")


def assert_true(name: str, condition: bool, msg: str = "") -> None:
    if condition:
        ok(name)
    else:
        fail(name, msg or "condition is False")


# ── Schema fixture ─────────────────────────────────────────────────────────────


def _make_conn():
    """Return an in-memory DuckDB connection with Phase 7 schema applied."""
    import duckdb

    conn = duckdb.connect(":memory:")

    schema_file = (
        Path(__file__).resolve().parent.parent
        / "sql" / "local" / "007_live_monitor_schema.sql"
    )
    sql = schema_file.read_text(encoding="utf-8")
    for chunk in sql.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        # Only execute chunks that contain at least one non-comment line
        has_sql = any(
            line.strip() and not line.strip().startswith("--")
            for line in chunk.splitlines()
        )
        if not has_sql:
            continue
        conn.execute(chunk)
    return conn


# ── Tests: schema ──────────────────────────────────────────────────────────────


def test_schema_tables():
    conn = _make_conn()
    tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
    for t in [
        "live_fixture_snapshots",
        "live_fixture_events",
        "live_pick_tracking",
        "live_notifications_log",
    ]:
        assert_true(f"schema_table_{t}", t in tables, f"Table {t!r} missing")
    conn.close()


# ── Tests: repo ────────────────────────────────────────────────────────────────


def test_repo_upsert_snapshot():
    from app.data.local.live_monitor_repo import upsert_live_snapshot, get_live_snapshot

    conn = _make_conn()
    upsert_live_snapshot(conn, {
        "provider_fixture_id": 1001,
        "status_short":  "1H",
        "status_elapsed": 30,
        "goals_home":    1,
        "goals_away":    0,
        "is_finished":   False,
    })
    snap = get_live_snapshot(conn, 1001)
    assert_true("snapshot_created", snap is not None)
    assert_eq("snapshot_goals_home", snap["goals_home"], 1)
    assert_eq("snapshot_status",     snap["status_short"], "1H")

    # Update
    upsert_live_snapshot(conn, {
        "provider_fixture_id": 1001,
        "status_short":  "2H",
        "status_elapsed": 75,
        "goals_home":    2,
        "goals_away":    1,
        "is_finished":   False,
    })
    snap2 = get_live_snapshot(conn, 1001)
    assert_eq("snapshot_updated_goals", snap2["goals_home"], 2)
    assert_eq("snapshot_updated_elapsed", snap2["status_elapsed"], 75)
    conn.close()


def test_repo_insert_event_idempotent():
    from app.data.local.live_monitor_repo import insert_live_event, get_events_for_fixture

    conn = _make_conn()
    row = {
        "event_id":            "1001_45_goal_home",
        "provider_fixture_id": 1001,
        "event_minute":        45,
        "event_type":          "goal",
        "team_side":           "home",
        "player_name":         "Messi",
    }
    insert_live_event(conn, row)
    insert_live_event(conn, row)  # should not raise or duplicate
    events = get_events_for_fixture(conn, 1001)
    assert_eq("event_count_idempotent", len(events), 1)
    assert_eq("event_player", events[0]["player_name"], "Messi")
    conn.close()


def test_repo_pick_tracking():
    from app.data.local.live_monitor_repo import upsert_pick_tracking, get_pick_tracking

    conn = _make_conn()
    upsert_pick_tracking(conn, {
        "pick_candidate_id":   42,
        "provider_fixture_id": 1001,
        "market_key":          "1X2",
        "selection":           "Home",
        "live_state":          "winning",
        "goals_home":          2,
        "goals_away":          0,
        "status_elapsed":      60,
        "status_short":        "2H",
    })
    t = get_pick_tracking(conn, 42)
    assert_true("tracking_created", t is not None)
    assert_eq("tracking_state", t["live_state"], "winning")

    # Update state
    upsert_pick_tracking(conn, {
        "pick_candidate_id":   42,
        "provider_fixture_id": 1001,
        "market_key":          "1X2",
        "selection":           "Home",
        "live_state":          "losing",
        "goals_home":          2,
        "goals_away":          3,
        "status_elapsed":      85,
        "status_short":        "2H",
    })
    t2 = get_pick_tracking(conn, 42)
    assert_eq("tracking_updated", t2["live_state"], "losing")
    conn.close()


def test_repo_notification_dedup():
    from app.data.local.live_monitor_repo import (
        notification_already_sent,
        log_notification,
    )

    conn = _make_conn()
    log_id = "42_state_winning"
    assert_true("notif_not_sent_yet", not notification_already_sent(conn, log_id))

    log_notification(conn, {
        "log_id":              log_id,
        "pick_candidate_id":   42,
        "provider_fixture_id": 1001,
        "notification_type":   "state_change",
        "state":               "winning",
    })
    assert_true("notif_sent", notification_already_sent(conn, log_id))

    # Duplicate insert should not raise
    log_notification(conn, {
        "log_id":              log_id,
        "pick_candidate_id":   42,
        "provider_fixture_id": 1001,
        "notification_type":   "state_change",
        "state":               "winning",
    })
    assert_true("notif_dedup_ok", notification_already_sent(conn, log_id))
    conn.close()


def test_repo_counts():
    from app.data.local.live_monitor_repo import (
        live_monitor_counts,
        upsert_live_snapshot,
        upsert_pick_tracking,
    )

    conn = _make_conn()
    upsert_live_snapshot(conn, {
        "provider_fixture_id": 2001,
        "status_short": "FT", "is_finished": True,
        "goals_home": 1, "goals_away": 1,
    })
    upsert_pick_tracking(conn, {
        "pick_candidate_id": 99,
        "provider_fixture_id": 2001,
        "market_key": "BTTS", "selection": "Yes",
        "live_state": "winning",
    })
    counts = live_monitor_counts(conn)
    assert_eq("counts_snapshots", counts["live_fixture_snapshots"], 1)
    assert_eq("counts_tracking",  counts["live_pick_tracking"], 1)
    conn.close()


# ── Tests: compute_live_pick_state ────────────────────────────────────────────


def test_state_1x2():
    from app.services.live_monitor_service import compute_live_pick_state as cps

    # Home winning
    assert_eq("1x2_home_win",  cps("1X2", "Home", 2, 0, "2H"), "winning")
    assert_eq("1x2_home_lose", cps("1X2", "Home", 0, 1, "2H"), "losing")
    assert_eq("1x2_home_open", cps("1X2", "Home", 1, 1, "2H"), "open")
    # Away
    assert_eq("1x2_away_win",  cps("1X2", "Away", 0, 1, "1H"), "winning")
    assert_eq("1x2_away_lose", cps("1X2", "Away", 2, 0, "1H"), "losing")
    # Draw at FT
    assert_eq("1x2_draw_ft",   cps("1X2", "Draw", 1, 1, "FT"), "winning")
    assert_eq("1x2_draw_live", cps("1X2", "Draw", 1, 1, "2H"), "open")
    assert_eq("1x2_draw_lose", cps("1X2", "Draw", 2, 1, "2H"), "losing")


def test_state_dc():
    from app.services.live_monitor_service import compute_live_pick_state as cps

    # 1X (Home or Draw)
    assert_eq("dc_1x_win_home",  cps("DC", "1X", 2, 1, "FT"), "winning")
    assert_eq("dc_1x_win_draw",  cps("DC", "1X", 1, 1, "FT"), "winning")
    assert_eq("dc_1x_lose",      cps("DC", "1X", 0, 1, "FT"), "losing")
    # X2 (Away or Draw)
    assert_eq("dc_x2_win_away",  cps("DC", "X2", 0, 2, "FT"), "winning")
    assert_eq("dc_x2_win_draw",  cps("DC", "X2", 2, 2, "FT"), "winning")
    assert_eq("dc_x2_lose",      cps("DC", "X2", 2, 0, "FT"), "losing")
    # 12 (No Draw)
    assert_eq("dc_12_win",       cps("DC", "12", 2, 1, "1H"), "winning")
    assert_eq("dc_12_lose_draw_ft", cps("DC", "12", 1, 1, "FT"), "losing")
    assert_eq("dc_12_open_draw_live", cps("DC", "12", 1, 1, "2H"), "open")
    # Alternative labels
    assert_eq("dc_homedraw",     cps("DC", "Home/Draw", 2, 0, "FT"), "winning")
    assert_eq("dc_homeaway",     cps("DC", "Home/Away", 2, 1, "FT"), "winning")


def test_state_ou25():
    from app.services.live_monitor_service import compute_live_pick_state as cps

    assert_eq("ou25_over_win",  cps("OU25", "Over 2.5", 2, 1, "2H"), "winning")
    assert_eq("ou25_over_open", cps("OU25", "Over 2.5", 1, 0, "2H"), "open")
    assert_eq("ou25_over_lose", cps("OU25", "Over 2.5", 1, 0, "FT"), "losing")
    assert_eq("ou25_under_open", cps("OU25", "Under 2.5", 1, 0, "2H"), "open")
    assert_eq("ou25_under_win",  cps("OU25", "Under 2.5", 1, 0, "FT"), "winning")
    assert_eq("ou25_under_lose", cps("OU25", "Under 2.5", 2, 1, "2H"), "losing")


def test_state_btts():
    from app.services.live_monitor_service import compute_live_pick_state as cps

    assert_eq("btts_yes_win",   cps("BTTS", "Yes", 1, 1, "2H"), "winning")
    assert_eq("btts_yes_open",  cps("BTTS", "Yes", 1, 0, "2H"), "open")
    assert_eq("btts_yes_lose",  cps("BTTS", "Yes", 1, 0, "FT"), "losing")
    assert_eq("btts_no_lose",   cps("BTTS", "No",  1, 1, "2H"), "losing")
    assert_eq("btts_no_open",   cps("BTTS", "No",  0, 1, "2H"), "open")
    assert_eq("btts_no_win",    cps("BTTS", "No",  0, 1, "FT"), "winning")


def test_state_to_outcome():
    from app.services.live_monitor_service import live_state_to_outcome

    assert_eq("outcome_winning", live_state_to_outcome("winning"), "win")
    assert_eq("outcome_losing",  live_state_to_outcome("losing"),  "loss")
    assert_eq("outcome_open",    live_state_to_outcome("open"),    None)


# ── Tests: dry-run produces no writes ─────────────────────────────────────────


def test_dry_run_no_writes():
    """run_live_monitor with dry_run=True must not touch DuckDB."""
    import asyncio
    from app.data.local.live_monitor_repo import live_monitor_counts

    conn = _make_conn()

    async def _run():
        # We monkey-patch _get_pending_fixture_ids to return empty
        import app.services.live_monitor_service as svc
        orig = svc._get_pending_fixture_ids
        svc._get_pending_fixture_ids = lambda *a, **kw: []
        try:
            return await svc.run_live_monitor(dry_run=True, execute=False)
        finally:
            svc._get_pending_fixture_ids = orig

    stats = asyncio.run(_run())
    assert_eq("dryrun_fixtures_checked", stats["fixtures_checked"], 0)
    assert_eq("dryrun_picks_settled",    stats["picks_settled"],    0)
    counts = live_monitor_counts(conn)
    assert_eq("dryrun_snapshots_empty", counts["live_fixture_snapshots"], 0)
    conn.close()


# ── Tests: settlement in-memory ───────────────────────────────────────────────


def test_settlement_idempotent():
    """Computing live state twice yields same result."""
    from app.services.live_monitor_service import compute_live_pick_state

    # Call twice, same input → same output
    state1 = compute_live_pick_state("1X2", "Home", 2, 0, "FT")
    state2 = compute_live_pick_state("1X2", "Home", 2, 0, "FT")
    assert_eq("settlement_idempotent", state1, state2)
    assert_eq("settlement_win",        state1, "winning")


def test_pick_tracking_for_fixture():
    from app.data.local.live_monitor_repo import (
        upsert_pick_tracking,
        get_pick_tracking_for_fixture,
    )

    conn = _make_conn()
    for pick_id, mkt, sel in [(10, "1X2", "Home"), (11, "OU25", "Over 2.5")]:
        upsert_pick_tracking(conn, {
            "pick_candidate_id":   pick_id,
            "provider_fixture_id": 3001,
            "market_key":          mkt,
            "selection":           sel,
            "live_state":          "open",
        })
    picks = get_pick_tracking_for_fixture(conn, 3001)
    assert_eq("fixture_picks_count", len(picks), 2)
    assert_true("fixture_picks_ids", {p["pick_candidate_id"] for p in picks} == {10, 11})
    conn.close()


def test_get_all_live_snapshots_empty():
    from app.data.local.live_monitor_repo import get_all_live_snapshots

    conn = _make_conn()
    snaps = get_all_live_snapshots(conn)
    assert_eq("empty_snapshots", snaps, [])
    conn.close()


def test_get_fixture_live_state_none():
    """get_fixture_live_state returns None when no data exists."""
    from app.services.live_monitor_service import get_fixture_live_state

    conn = _make_conn()
    result = get_fixture_live_state(conn, 99999)
    assert_eq("live_state_none", result, None)
    conn.close()


def test_get_fixture_live_state_with_data():
    """get_fixture_live_state returns snapshot + picks."""
    from app.data.local.live_monitor_repo import upsert_live_snapshot, upsert_pick_tracking
    from app.services.live_monitor_service import get_fixture_live_state

    conn = _make_conn()
    upsert_live_snapshot(conn, {
        "provider_fixture_id": 4001,
        "status_short": "2H",
        "status_elapsed": 70,
        "goals_home": 1,
        "goals_away": 0,
        "is_finished": False,
    })
    upsert_pick_tracking(conn, {
        "pick_candidate_id":   55,
        "provider_fixture_id": 4001,
        "market_key":          "1X2",
        "selection":           "Home",
        "live_state":          "winning",
    })
    result = get_fixture_live_state(conn, 4001)
    assert_true("live_state_exists", result is not None)
    assert_eq("live_state_goals",    result["goals_home"], 1)
    assert_eq("live_state_picks",    len(result["picks"]), 1)
    assert_eq("live_state_pick_st",  result["picks"][0]["live_state"], "winning")
    conn.close()


def test_live_summary():
    """get_live_summary returns list with picks nested."""
    from app.data.local.live_monitor_repo import upsert_live_snapshot, upsert_pick_tracking
    from app.services.live_monitor_service import get_live_summary

    conn = _make_conn()
    for pfid in [5001, 5002]:
        upsert_live_snapshot(conn, {
            "provider_fixture_id": pfid,
            "status_short": "1H",
            "goals_home": 0, "goals_away": 0,
            "is_finished": False,
        })
        upsert_pick_tracking(conn, {
            "pick_candidate_id":   pfid * 10,
            "provider_fixture_id": pfid,
            "market_key":          "1X2",
            "selection":           "Home",
            "live_state":          "open",
        })
    summary = get_live_summary(conn)
    assert_eq("live_summary_count", len(summary), 2)
    for s in summary:
        assert_true(f"live_summary_picks_{s['provider_fixture_id']}", len(s["picks"]) == 1)
    conn.close()


# ── Runner ────────────────────────────────────────────────────────────────────


def run_all() -> None:
    tests = [
        test_schema_tables,
        test_repo_upsert_snapshot,
        test_repo_insert_event_idempotent,
        test_repo_pick_tracking,
        test_repo_notification_dedup,
        test_repo_counts,
        test_state_1x2,
        test_state_dc,
        test_state_ou25,
        test_state_btts,
        test_state_to_outcome,
        test_dry_run_no_writes,
        test_settlement_idempotent,
        test_pick_tracking_for_fixture,
        test_get_all_live_snapshots_empty,
        test_get_fixture_live_state_none,
        test_get_fixture_live_state_with_data,
        test_live_summary,
    ]

    group = None
    for fn in tests:
        grp = fn.__name__.split("_")[1]
        if grp != group:
            group = grp
            print(f"\n  [{grp.upper()}]")
        try:
            fn()
        except Exception:
            fail(fn.__name__, traceback.format_exc().strip().splitlines()[-1])

    total = len(_passed) + len(_failed)
    print()
    print("=" * 48)
    if _failed:
        print(f"  RESULTADO: {len(_passed)}/{total} passed — {len(_failed)} FALLIDOS")
        for name in _failed:
            print(f"    FAIL: {name}")
    else:
        print(f"  RESULTADO: {total}/{total} passed OK")
    print("=" * 48)

    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    print()
    print("=" * 48)
    print("  TEST LIVE MONITOR (Phase 7)")
    print("=" * 48)
    run_all()
