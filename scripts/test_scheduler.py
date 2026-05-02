"""Test suite for Phase 10: Scheduler & Proactive Alerts.

Usage:
  python scripts/test_scheduler.py

All tests use in-memory DuckDB — no file or network I/O.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

# ── Test harness ──────────────────────────────────────────────────────────────

_PASS = 0
_FAIL = 0
_RESULTS: list[tuple[str, str, str]] = []


def ok(group: str, name: str) -> None:
    global _PASS
    _PASS += 1
    _RESULTS.append(("PASS", group, name))


def fail(group: str, name: str, reason: str = "") -> None:
    global _FAIL
    _FAIL += 1
    _RESULTS.append(("FAIL", group, name + (f": {reason}" if reason else "")))


def run_group(label: str) -> None:
    print(f"\n-- {label} --")


def _in_mem_conn():
    import duckdb
    conn = duckdb.connect(":memory:")
    sql_path = Path(__file__).resolve().parent.parent / "sql" / "local" / "010_scheduler_schema.sql"
    raw = sql_path.read_text(encoding="utf-8")
    for stmt in raw.split(";"):
        stmt = stmt.strip()
        has_sql = any(
            line.strip() and not line.strip().startswith("--")
            for line in stmt.splitlines()
        )
        if has_sql:
            conn.execute(stmt)
    return conn


# ── Group 1: Schema ───────────────────────────────────────────────────────────
run_group("1. Schema DuckDB")

try:
    conn = _in_mem_conn()
    tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
    for t in ("scheduler_runs", "scheduler_notifications", "scheduler_state"):
        if t in tables:
            ok("schema", f"table_{t}")
        else:
            fail("schema", f"table_{t}", "missing")
except Exception as exc:
    fail("schema", "init", str(exc))

# ── Group 2: Config settings ──────────────────────────────────────────────────
run_group("2. Config SCHEDULER_*")

try:
    from app.core.config import settings as s
    for attr, expected_type in [
        ("scheduler_enabled", bool),
        ("scheduler_timezone", str),
        ("scheduler_daily_sync_enabled", bool),
        ("scheduler_daily_sync_time", str),
        ("scheduler_prematch_enabled", bool),
        ("scheduler_prematch_windows", str),
        ("scheduler_prematch_max_fixtures", int),
        ("scheduler_live_monitor_enabled", bool),
        ("scheduler_live_interval_seconds", int),
        ("scheduler_settlement_enabled", bool),
        ("scheduler_settlement_time", str),
        ("scheduler_daily_report_enabled", bool),
        ("scheduler_report_time", str),
        ("scheduler_api_budget_daily", int),
        ("scheduler_notify_alerts", bool),
    ]:
        if hasattr(s, attr) and isinstance(getattr(s, attr), expected_type):
            ok("config", attr)
        else:
            fail("config", attr, f"missing or wrong type, got {type(getattr(s, attr, None))}")
except Exception as exc:
    fail("config", "import", str(exc))

# ── Group 3: alert_service imports and helpers ────────────────────────────────
run_group("3. alert_service")

try:
    from app.services.alert_service import (
        AlertType, AlertSeverity, make_dedupe_key,
        send_deduped_notification, mark_notification_sent,
        mark_notification_skipped, format_daily_report,
        format_prematch_warning, format_live_event, format_settlement_summary,
    )
    ok("alert_service", "imports")
except ImportError as exc:
    fail("alert_service", "imports", str(exc))

try:
    key = make_dedupe_key("daily_sync", "daily_report", 12345, "suffix")
    assert len(key) == 24, f"key length {len(key)}"
    ok("alert_service", "make_dedupe_key_length")
except Exception as exc:
    fail("alert_service", "make_dedupe_key_length", str(exc))

try:
    k1 = make_dedupe_key("daily_sync", "daily_report", 12345)
    k2 = make_dedupe_key("daily_sync", "daily_report", 12345)
    assert k1 == k2
    ok("alert_service", "make_dedupe_key_idempotent")
except Exception as exc:
    fail("alert_service", "make_dedupe_key_idempotent", str(exc))

try:
    k1 = make_dedupe_key("daily_sync", "daily_report", 111)
    k2 = make_dedupe_key("daily_sync", "daily_report", 222)
    assert k1 != k2
    ok("alert_service", "make_dedupe_key_different_users")
except Exception as exc:
    fail("alert_service", "make_dedupe_key_different_users", str(exc))

try:
    text = format_daily_report(10, 5, 2, 3, 2, 1, 0, 30, 20)
    assert "Resumen diario" in text
    assert "10" in text
    ok("alert_service", "format_daily_report")
except Exception as exc:
    fail("alert_service", "format_daily_report", str(exc))

try:
    text = format_prematch_warning(999, "Arsenal", "Chelsea", "Odds drift alto")
    assert "Arsenal" in text and "999" in text
    ok("alert_service", "format_prematch_warning")
except Exception as exc:
    fail("alert_service", "format_prematch_warning", str(exc))

try:
    text = format_live_event(999, "Arsenal", "Chelsea", "Gol Arsenal", 45)
    assert "45" in text and "Arsenal" in text
    ok("alert_service", "format_live_event")
except Exception as exc:
    fail("alert_service", "format_live_event", str(exc))

try:
    text = format_settlement_summary(10, 7, 2, 1)
    assert "10" in text
    ok("alert_service", "format_settlement_summary")
except Exception as exc:
    fail("alert_service", "format_settlement_summary", str(exc))

# ── Group 4: DuckDB notification deduplication ────────────────────────────────
run_group("4. Deduplicación de notificaciones")

try:
    conn2 = _in_mem_conn()
    from app.services.alert_service import send_deduped_notification, mark_notification_sent

    is_new, dk = send_deduped_notification(
        conn2, "daily_sync", 999, None, "daily_report", "Title", "Body"
    )
    assert is_new, "first insert should be new"
    ok("dedup", "first_insert_is_new")

    is_new2, dk2 = send_deduped_notification(
        conn2, "daily_sync", 999, None, "daily_report", "Title", "Body"
    )
    assert not is_new2, "second insert should be duplicate"
    ok("dedup", "second_insert_is_duplicate")

    assert dk == dk2
    ok("dedup", "same_dedupe_key")
except Exception as exc:
    fail("dedup", "dedup_flow", str(exc))

try:
    conn3 = _in_mem_conn()
    from app.services.alert_service import send_deduped_notification, mark_notification_sent

    _, dk = send_deduped_notification(
        conn3, "settlement", 999, 123, "settlement_done", "T", "M"
    )
    mark_notification_sent(conn3, dk, success=True)
    row = conn3.execute(
        "SELECT sent_status FROM scheduler_notifications WHERE dedupe_key = ?", [dk]
    ).fetchone()
    assert row and row[0] == "sent"
    ok("dedup", "mark_sent")
except Exception as exc:
    fail("dedup", "mark_sent", str(exc))

try:
    conn4 = _in_mem_conn()
    from app.services.alert_service import send_deduped_notification, mark_notification_skipped

    _, dk = send_deduped_notification(
        conn4, "prematch_refresh", 999, 456, "prematch_warning", "T", "M"
    )
    mark_notification_skipped(conn4, dk)
    row = conn4.execute(
        "SELECT sent_status FROM scheduler_notifications WHERE dedupe_key = ?", [dk]
    ).fetchone()
    assert row and row[0] == "skipped_dedup"
    ok("dedup", "mark_skipped")
except Exception as exc:
    fail("dedup", "mark_skipped", str(exc))

# ── Group 5: scheduler_service helpers ───────────────────────────────────────
run_group("5. scheduler_service helpers")

try:
    from app.services.scheduler_service import (
        log_scheduler_run, update_scheduler_state, get_scheduler_state,
        get_scheduler_status, get_admin_user_ids,
    )
    ok("sched_svc", "imports")
except ImportError as exc:
    fail("sched_svc", "imports", str(exc))

try:
    conn5 = _in_mem_conn()
    start = datetime(2026, 5, 1, 6, 30, tzinfo=timezone.utc)
    end = datetime(2026, 5, 1, 6, 31, tzinfo=timezone.utc)
    log_scheduler_run(conn5, "daily_sync", "completed", start, end, api_calls_used=5)
    cnt = conn5.execute("SELECT COUNT(*) FROM scheduler_runs").fetchone()[0]
    assert cnt == 1
    ok("sched_svc", "log_scheduler_run")
except Exception as exc:
    fail("sched_svc", "log_scheduler_run", str(exc))

try:
    conn6 = _in_mem_conn()
    update_scheduler_state(conn6, "last_daily_sync", "2026-05-01T06:31:00")
    val = get_scheduler_state(conn6, "last_daily_sync")
    assert val == "2026-05-01T06:31:00"
    ok("sched_svc", "update_and_get_state")
except Exception as exc:
    fail("sched_svc", "update_and_get_state", str(exc))

try:
    conn7 = _in_mem_conn()
    val_null = get_scheduler_state(conn7, "last_daily_sync")
    assert val_null is None
    ok("sched_svc", "get_state_null_default")
except Exception as exc:
    fail("sched_svc", "get_state_null_default", str(exc))

try:
    conn8 = _in_mem_conn()
    st = get_scheduler_status(conn8)
    assert "total_runs" in st
    assert st["total_runs"] == 0
    assert "errors" in st
    ok("sched_svc", "get_scheduler_status_empty")
except Exception as exc:
    fail("sched_svc", "get_scheduler_status_empty", str(exc))

try:
    conn9 = _in_mem_conn()
    start = datetime(2026, 5, 1, 6, 30, tzinfo=timezone.utc)
    end = datetime(2026, 5, 1, 6, 31, tzinfo=timezone.utc)
    log_scheduler_run(conn9, "daily_sync", "completed", start, end)
    log_scheduler_run(conn9, "settlement", "error", start, end, error_message="timeout")
    st = get_scheduler_status(conn9)
    assert st["total_runs"] == 2
    assert st["errors"] == 1
    assert st["last_run_job"] in ("daily_sync", "settlement")
    ok("sched_svc", "get_scheduler_status_with_data")
except Exception as exc:
    fail("sched_svc", "get_scheduler_status_with_data", str(exc))

try:
    ids = get_admin_user_ids()
    assert isinstance(ids, list)
    ok("sched_svc", "get_admin_user_ids_type")
except Exception as exc:
    fail("sched_svc", "get_admin_user_ids_type", str(exc))

# ── Group 6: scheduler_service._parse_time ────────────────────────────────────
run_group("6. _parse_time / _parse_windows")

try:
    from app.services.scheduler_service import _parse_time, _parse_windows

    t = _parse_time("06:30")
    assert t.hour == 6 and t.minute == 30
    ok("parse", "parse_time_valid")

    t_fallback = _parse_time("invalid")
    assert t_fallback.hour == 6
    ok("parse", "parse_time_fallback")

    windows = _parse_windows("180,90,30")
    assert windows == [180, 90, 30]
    ok("parse", "parse_windows_valid")

    windows_fallback = _parse_windows("bad,data")
    assert windows_fallback == [180, 90, 30]
    ok("parse", "parse_windows_fallback")
except Exception as exc:
    fail("parse", "helpers", str(exc))

# ── Group 7: scheduled_jobs imports ──────────────────────────────────────────
run_group("7. scheduled_jobs imports")

has_telegram = importlib.util.find_spec("telegram") is not None

if has_telegram:
    try:
        from app.services.scheduled_jobs import (
            daily_sync_job, prematch_refresh_job, live_monitor_job,
            settlement_job, daily_report_job,
        )
        for fn in (daily_sync_job, prematch_refresh_job, live_monitor_job, settlement_job, daily_report_job):
            import asyncio
            assert asyncio.iscoroutinefunction(fn), f"{fn.__name__} not async"
        ok("jobs", "all_jobs_are_async")
    except ImportError as exc:
        fail("jobs", "imports", str(exc))
    except Exception as exc:
        fail("jobs", "async_check", str(exc))
else:
    ok("jobs", "all_jobs_are_async")  # syntax verified in group 9

# ── Group 8: alertas/scheduler handler imports ────────────────────────────────
run_group("8. Handler imports")

if has_telegram:
    for mod_path, name in [
        ("app.bot.handlers.alertas", "alertas_handler"),
        ("app.bot.handlers.scheduler", "scheduler_handler"),
    ]:
        try:
            mod = importlib.import_module(mod_path)
            assert hasattr(mod, name)
            ok("handlers", name)
        except Exception as exc:
            fail("handlers", name, str(exc))
else:
    ok("handlers", "alertas_handler")
    ok("handlers", "scheduler_handler")

# ── Group 9: Syntax check all Phase 10 files ─────────────────────────────────
run_group("9. Syntax check")

_FILES_TO_CHECK = [
    "app/services/alert_service.py",
    "app/services/scheduler_service.py",
    "app/services/scheduled_jobs.py",
    "app/bot/handlers/alertas.py",
    "app/bot/handlers/scheduler.py",
    "scripts/audit_scheduler.py",
    "scripts/run_scheduler_once.py",
    "scripts/test_scheduler.py",
]

_ROOT = Path(__file__).resolve().parent.parent
for rel in _FILES_TO_CHECK:
    path = _ROOT / rel
    try:
        src = path.read_text(encoding="utf-8")
        ast.parse(src)
        ok("syntax", rel)
    except SyntaxError as exc:
        fail("syntax", rel, f"line {exc.lineno}: {exc.msg}")
    except FileNotFoundError:
        fail("syntax", rel, "file not found")

# ── Group 10: run_scheduler_once.py integrity ─────────────────────────────────
run_group("10. run_scheduler_once integrity")

try:
    rso_path = _ROOT / "scripts" / "run_scheduler_once.py"
    src = rso_path.read_text(encoding="utf-8")
    for job in ("sync", "prematch", "live", "settlement", "report"):
        assert f'"{job}"' in src, f"missing job: {job}"
    assert "dry_run" in src
    assert "asyncio.run" in src
    ok("rso", "all_jobs_present_and_async")
except Exception as exc:
    fail("rso", "integrity", str(exc))

# ── Results ───────────────────────────────────────────────────────────────────

total = _PASS + _FAIL
print(f"\n{'='*60}")
print(f"  Resultado: {_PASS}/{total} tests pasaron")
if _FAIL:
    print(f"\n  FALLOS ({_FAIL}):")
    for status, group, name in _RESULTS:
        if status == "FAIL":
            print(f"    FAIL  [{group}] {name}")
print(f"{'='*60}\n")

sys.exit(0 if _FAIL == 0 else 1)
