"""API budget tracking and quota management.

Per-call records are appended to a local JSONL file so they add zero latency
to API calls and survive process restarts without a Supabase round-trip.

Rate-limit state (remaining/limit) is read from client.py's in-memory
_rate_state dict (updated from every HTTP response header).

Priority levels and minimum remaining quota required:
  critical  — 0    (always allowed: settlement, schema health)
  high      — 1000 (daily sync, forced refreshes)
  medium    — 2000 (normal enrichment)
  low       — 3500 (optional context: injuries, team stats)
  batch     — 3500 (historical back-fill)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

_LOG_PATH = Path(settings.local_db_path).parent / "api_usage_log.jsonl"

_THRESHOLDS: dict[str, int] = {
    "critical": 0,
    "high": 1000,
    "medium": 2000,
    "low": 3500,
    "batch": 3500,
}


# ── Public API ────────────────────────────────────────────────────────────────


def get_log_path() -> Path:
    return _LOG_PATH


def record_call(
    endpoint: str,
    duration_ms: int,
    status_code: int,
    results_count: int = 0,
    params_hash: str = "",
    source_script: str = "",
    priority: str = "medium",
) -> None:
    """Append one API call record to the JSONL log. Never raises."""
    try:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "endpoint": endpoint,
            "params_hash": params_hash,
            "duration_ms": duration_ms,
            "status_code": status_code,
            "results_count": results_count,
            "source_script": source_script,
            "priority": priority,
        }
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception as exc:
        logger.debug("api_budget_service.record_call failed silently: %s", exc)


def get_today_log() -> list[dict]:
    """Return all JSONL entries timestamped today (UTC)."""
    today = datetime.now(timezone.utc).date().isoformat()
    if not _LOG_PATH.exists():
        return []
    entries: list[dict] = []
    try:
        with _LOG_PATH.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("ts", "").startswith(today):
                        entries.append(entry)
                except json.JSONDecodeError:
                    pass
    except Exception as exc:
        logger.debug("api_budget_service.get_today_log error: %s", exc)
    return entries


def get_log_for_days(days: int) -> list[dict]:
    """Return JSONL entries for the last N calendar days (UTC)."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if not _LOG_PATH.exists():
        return []
    entries: list[dict] = []
    try:
        with _LOG_PATH.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("ts", "") >= cutoff:
                        entries.append(entry)
                except json.JSONDecodeError:
                    pass
    except Exception as exc:
        logger.debug("api_budget_service.get_log_for_days error: %s", exc)
    return entries


def get_daily_state() -> dict:
    """Return current quota state.

    Reads remaining/limit from the in-memory rate state (updated on every API
    call). Falls back to JSONL log count when the rate state is not populated
    yet (i.e. no API calls made this process).

    Returns:
        remaining        — requests remaining today (None if unknown)
        limit            — daily quota (None if unknown)
        used_today       — successful calls logged in JSONL today
        status           — "ok" | "warning" | "critical" | "unknown"
        can_run_by_priority — {priority: bool}
        log_entries_today — total JSONL entries today (incl. errors)
        rate_state       — raw in-memory dict from client.py
    """
    rate: dict = {}
    try:
        from app.data.api_football.client import get_rate_limit_state
        rate = get_rate_limit_state()
    except Exception:
        pass

    remaining: int | None = rate.get("requests_remaining")
    limit: int | None = rate.get("requests_limit")

    today_entries = get_today_log()
    used_today = len([e for e in today_entries if e.get("status_code", 200) < 400])

    if remaining is None and limit is not None and used_today > 0:
        remaining = limit - used_today

    if remaining is None:
        status = "unknown"
    elif remaining < 100:
        status = "critical"
    elif remaining < 500:
        status = "warning"
    else:
        status = "ok"

    can_run: dict[str, bool] = {}
    for priority, threshold in _THRESHOLDS.items():
        if remaining is None:
            can_run[priority] = priority == "critical"
        else:
            can_run[priority] = remaining >= threshold

    return {
        "remaining": remaining,
        "limit": limit,
        "used_today": used_today,
        "status": status,
        "can_run_by_priority": can_run,
        "log_entries_today": len(today_entries),
        "rate_state": rate,
    }


def can_run(priority: str = "medium", source: str = "", allow_unknown: bool = True) -> bool:
    """Return True if a call with this priority is allowed under current quota.

    When allow_unknown=True (default), an unknown quota state lets through all
    priorities — the first API call will reveal the real remaining count.
    Set allow_unknown=False to be conservative and block when state is unknown.
    """
    state = get_daily_state()
    if state["status"] == "unknown" and allow_unknown:
        logger.debug("Budget state unknown — allowing %s (source=%s)", priority, source)
        return True
    allowed = state["can_run_by_priority"].get(priority, False)
    if not allowed:
        remaining = state.get("remaining", "?")
        threshold = _THRESHOLDS.get(priority, 0)
        logger.info(
            "Budget gate blocked %s (source=%s): remaining=%s < threshold=%d",
            priority, source, remaining, threshold,
        )
    return allowed
