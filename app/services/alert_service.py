"""Phase 10: Alert types, severity, deduplication, and message formatters."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)


class AlertType(str, Enum):
    PICK_PUBLISHED = "pick_published"
    PARLAY_READY = "parlay_ready"
    LIVE_GOAL = "live_goal"
    LIVE_RESULT = "live_result"
    DAILY_REPORT = "daily_report"
    SETTLEMENT_DONE = "settlement_done"
    PREMATCH_WARNING = "prematch_warning"
    SYSTEM_ERROR = "system_error"


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


def make_dedupe_key(
    job_key: str,
    notification_type: str,
    user_id: int,
    suffix: str = "",
) -> str:
    """Generate a 24-char stable dedupe key scoped to today."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw = f"{today}:{job_key}:{notification_type}:{user_id}:{suffix}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def send_deduped_notification(
    conn: "duckdb.DuckDBPyConnection",
    job_key: str,
    user_id: int,
    fixture_id: int | None,
    notification_type: str,
    title: str,
    message_text: str,
    dedupe_suffix: str = "",
    metadata: dict | None = None,
) -> tuple[bool, str]:
    """
    Insert a notification record if it hasn't been sent today.

    Returns (is_new, dedupe_key). Caller should send the Telegram message
    and then call mark_notification_sent() or mark_notification_skipped().
    """
    dedupe_key = make_dedupe_key(job_key, notification_type, user_id, dedupe_suffix)
    try:
        existing = conn.execute(
            "SELECT COUNT(*) FROM scheduler_notifications WHERE dedupe_key = ?",
            [dedupe_key],
        ).fetchone()[0]
        if existing > 0:
            return False, dedupe_key

        conn.execute(
            """
            INSERT INTO scheduler_notifications
                (id, job_key, user_id, fixture_id, notification_type,
                 title, message_text, sent_status, dedupe_key, metadata_json)
            VALUES
                (nextval('scheduler_notifications_seq'), ?, ?, ?, ?,
                 ?, ?, 'pending', ?, ?)
            """,
            [
                job_key, user_id, fixture_id, notification_type,
                title, message_text, dedupe_key,
                json.dumps(metadata) if metadata else None,
            ],
        )
        return True, dedupe_key
    except Exception as exc:
        logger.error("send_deduped_notification failed: %s", exc)
        return False, dedupe_key


def mark_notification_sent(
    conn: "duckdb.DuckDBPyConnection",
    dedupe_key: str,
    success: bool = True,
) -> None:
    status = "sent" if success else "failed"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn.execute(
            "UPDATE scheduler_notifications SET sent_status = ?, sent_at = ? WHERE dedupe_key = ?",
            [status, now, dedupe_key],
        )
    except Exception as exc:
        logger.debug("mark_notification_sent failed: %s", exc)


def mark_notification_skipped(
    conn: "duckdb.DuckDBPyConnection",
    dedupe_key: str,
) -> None:
    try:
        conn.execute(
            "UPDATE scheduler_notifications SET sent_status = 'skipped_dedup' WHERE dedupe_key = ?",
            [dedupe_key],
        )
    except Exception as exc:
        logger.debug("mark_notification_skipped failed: %s", exc)


# ── Message formatters ────────────────────────────────────────────────────────

def format_daily_report(
    picks_today: int,
    picks_published: int,
    parlays_today: int,
    settled_today: int,
    win: int,
    loss: int,
    void: int,
    api_calls_used: int,
    budget_remaining: int,
) -> str:
    win_rate = f"{win/(win+loss)*100:.0f}%" if (win + loss) > 0 else "—"
    return (
        "📊 <b>Resumen diario</b>\n\n"
        f"Picks hoy: <b>{picks_today}</b> candidatos · {picks_published} publicados\n"
        f"Parlays: <b>{parlays_today}</b>\n"
        f"Liquidados: {settled_today} ({win}W / {loss}L / {void}V) — {win_rate} win\n"
        f"\n⚙️ API: {api_calls_used} llamadas · {budget_remaining} restantes"
    )


def format_prematch_warning(
    fixture_id: int,
    home: str,
    away: str,
    reason: str,
) -> str:
    return (
        f"⚠️ <b>Alerta prematch</b>\n"
        f"{home} vs {away} (#{fixture_id})\n"
        f"{reason}"
    )


def format_live_event(
    fixture_id: int,
    home: str,
    away: str,
    event: str,
    minute: int,
) -> str:
    return (
        f"⚽ <b>Live: {home} vs {away}</b>\n"
        f"Min {minute}': {event}\n"
        f"Fixture #{fixture_id}"
    )


def format_settlement_summary(settled: int, win: int, loss: int, void: int) -> str:
    win_rate = f"{win/(win+loss)*100:.0f}%" if (win + loss) > 0 else "—"
    return (
        f"💰 <b>Liquidación completada</b>\n"
        f"{settled} picks resueltos — {win}W / {loss}L / {void}V ({win_rate} win)"
    )
