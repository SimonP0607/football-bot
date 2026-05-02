"""Phase 10: Scheduler service — job registration, run logging, state, notifications."""

from __future__ import annotations

import json
import logging
from datetime import datetime, time, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb
    from telegram.ext import Application

from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_admin_user_ids() -> list[int]:
    uid = settings.telegram_allowed_user_id
    return [uid] if uid else []


def _parse_time(time_str: str) -> time:
    try:
        parts = time_str.strip().split(":")
        return time(int(parts[0]), int(parts[1]))
    except Exception:
        return time(6, 30)


def _parse_windows(windows_str: str) -> list[int]:
    try:
        result = [int(x.strip()) for x in windows_str.split(",") if x.strip().isdigit()]
        return result if result else [180, 90, 30]
    except Exception:
        return [180, 90, 30]


# ── Run logging ───────────────────────────────────────────────────────────────

def log_scheduler_run(
    conn: "duckdb.DuckDBPyConnection",
    job_key: str,
    status: str,
    started_at: datetime,
    finished_at: datetime,
    api_calls_used: int = 0,
    messages_sent: int = 0,
    error_message: str | None = None,
    metadata: dict | None = None,
) -> None:
    duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    try:
        conn.execute(
            """
            INSERT INTO scheduler_runs
                (id, job_key, status, started_at, finished_at,
                 duration_ms, api_calls_used, messages_sent,
                 error_message, metadata_json)
            VALUES
                (nextval('scheduler_runs_seq'), ?, ?, ?, ?,
                 ?, ?, ?, ?, ?)
            """,
            [
                job_key, status,
                started_at.strftime("%Y-%m-%d %H:%M:%S"),
                finished_at.strftime("%Y-%m-%d %H:%M:%S"),
                duration_ms, api_calls_used, messages_sent,
                error_message,
                json.dumps(metadata) if metadata else None,
            ],
        )
    except Exception as exc:
        logger.warning("log_scheduler_run failed: %s", exc)


# ── State store ───────────────────────────────────────────────────────────────

def update_scheduler_state(
    conn: "duckdb.DuckDBPyConnection",
    key: str,
    value: Any,
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn.execute(
            "INSERT OR REPLACE INTO scheduler_state (key, value_json, updated_at) VALUES (?, ?, ?)",
            [key, json.dumps(value), now],
        )
    except Exception as exc:
        logger.debug("update_scheduler_state failed for %s: %s", key, exc)


def get_scheduler_state(
    conn: "duckdb.DuckDBPyConnection",
    key: str,
) -> Any:
    try:
        row = conn.execute(
            "SELECT value_json FROM scheduler_state WHERE key = ?", [key]
        ).fetchone()
        return json.loads(row[0]) if row and row[0] and row[0] != "null" else None
    except Exception:
        return None


# ── Telegram senders ──────────────────────────────────────────────────────────

async def send_telegram_message(
    application: "Application",
    user_id: int,
    text: str,
) -> bool:
    try:
        await application.bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="HTML",
        )
        return True
    except Exception as exc:
        logger.error("send_telegram_message to %s failed: %s", user_id, exc)
        return False


async def send_admin_notification(application: "Application", text: str) -> int:
    """Send a message to all admin users. Returns count of successful sends."""
    admin_ids = get_admin_user_ids()
    sent = 0
    for uid in admin_ids:
        ok = await send_telegram_message(application, uid, text)
        if ok:
            sent += 1
    return sent


# ── Status ────────────────────────────────────────────────────────────────────

def get_scheduler_status(conn: "duckdb.DuckDBPyConnection") -> dict:
    try:
        total_runs = conn.execute("SELECT COUNT(*) FROM scheduler_runs").fetchone()[0]
        errors = conn.execute(
            "SELECT COUNT(*) FROM scheduler_runs WHERE status = 'error'"
        ).fetchone()[0]
        last_run = conn.execute(
            "SELECT job_key, status, finished_at FROM scheduler_runs ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        total_notif = conn.execute(
            "SELECT COUNT(*) FROM scheduler_notifications"
        ).fetchone()[0]
        pending_notif = conn.execute(
            "SELECT COUNT(*) FROM scheduler_notifications WHERE sent_status = 'pending'"
        ).fetchone()[0]
        return {
            "total_runs": total_runs,
            "errors": errors,
            "last_run_job": last_run[0] if last_run else None,
            "last_run_status": last_run[1] if last_run else None,
            "last_run_at": str(last_run[2]) if last_run else None,
            "total_notifications": total_notif,
            "pending_notifications": pending_notif,
        }
    except Exception as exc:
        return {"error": str(exc)}


# ── Job registration ──────────────────────────────────────────────────────────

def setup_scheduler(application: "Application") -> None:
    """Register all scheduled jobs on the PTB JobQueue (if available)."""
    if not settings.scheduler_enabled:
        logger.info("Scheduler desactivado (SCHEDULER_ENABLED=false).")
        return

    job_queue = application.job_queue
    if job_queue is None:
        logger.warning(
            "JobQueue no disponible — instala python-telegram-bot[job-queue]. "
            "El scheduler no se activará."
        )
        return

    from app.services.scheduled_jobs import (
        daily_sync_job,
        prematch_refresh_job,
        live_monitor_job,
        settlement_job,
        daily_report_job,
    )

    registered: list[str] = []

    if settings.scheduler_daily_sync_enabled:
        t = _parse_time(settings.scheduler_daily_sync_time)
        job_queue.run_daily(
            daily_sync_job,
            time=t,
            name="daily_sync",
            data={"application": application},
        )
        registered.append(f"daily_sync@{t}")

    if settings.scheduler_prematch_enabled:
        for w in _parse_windows(settings.scheduler_prematch_windows):
            job_queue.run_repeating(
                prematch_refresh_job,
                interval=w * 60,
                name=f"prematch_{w}m",
                data={"application": application, "window_minutes": w},
            )
            registered.append(f"prematch@{w}m")

    if settings.scheduler_live_monitor_enabled:
        interval = settings.scheduler_live_interval_seconds
        job_queue.run_repeating(
            live_monitor_job,
            interval=interval,
            name="live_monitor",
            data={"application": application},
        )
        registered.append(f"live@{interval}s")

    if settings.scheduler_settlement_enabled:
        t = _parse_time(settings.scheduler_settlement_time)
        job_queue.run_daily(
            settlement_job,
            time=t,
            name="settlement",
            data={"application": application},
        )
        registered.append(f"settlement@{t}")

    if settings.scheduler_daily_report_enabled:
        t = _parse_time(settings.scheduler_report_time)
        job_queue.run_daily(
            daily_report_job,
            time=t,
            name="daily_report",
            data={"application": application},
        )
        registered.append(f"daily_report@{t}")

    if registered:
        logger.info("Scheduler: %d jobs registrados — %s", len(registered), " · ".join(registered))
    else:
        logger.info("Scheduler habilitado pero ningún job individual está activo.")
