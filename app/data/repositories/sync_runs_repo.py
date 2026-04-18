"""Supabase repository for api_sync_runs and api_usage_snapshots.

api_sync_runs: one row per sync job execution (audit trail).
api_usage_snapshots: rate-limit header snapshots per notable API call group.

These tables let the /estado command show the last sync status and API quota
usage without making a live API call at query time.
"""

import logging
from datetime import datetime, timezone

from app.data.repositories.supabase_client import get_supabase

logger = logging.getLogger(__name__)


# ── Sync runs ─────────────────────────────────────────────────────────────────


def start_sync_run(phase: str) -> int | None:
    """Insert a new sync run row with status='running'.

    Returns the internal row ID, or None on error.
    """
    client = get_supabase()
    try:
        result = (
            client.table("api_sync_runs")
            .insert({"phase": phase, "status": "running"})
            .execute()
        )
        row = result.data[0] if result.data else {}
        run_id = row.get("id")
        logger.debug("sync_run started: id=%s phase=%s", run_id, phase)
        return run_id
    except Exception as exc:
        logger.warning("No se pudo registrar sync_run (continuando): %s", exc)
        return None


def finish_sync_run(
    run_id: int | None,
    *,
    status: str = "completed",
    leagues_synced: int = 0,
    fixtures_synced: int = 0,
    odds_rows_synced: int = 0,
    api_calls_made: int = 0,
    error_message: str | None = None,
    summary_json: dict | None = None,
) -> None:
    """Update a sync run row with final status and counters.

    Safe to call even if run_id is None (no-op in that case).
    """
    if run_id is None:
        return
    client = get_supabase()
    try:
        client.table("api_sync_runs").update(
            {
                "status": status,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "leagues_synced": leagues_synced,
                "fixtures_synced": fixtures_synced,
                "odds_rows_synced": odds_rows_synced,
                "api_calls_made": api_calls_made,
                "error_message": error_message,
                "summary_json": summary_json,
            }
        ).eq("id", run_id).execute()
        logger.debug(
            "sync_run finished: id=%s status=%s fixtures=%d",
            run_id, status, fixtures_synced,
        )
    except Exception as exc:
        logger.warning("No se pudo actualizar sync_run id=%s: %s", run_id, exc)


def get_last_sync_run(phase: str | None = None) -> dict | None:
    """Return the most recent sync run row, optionally filtered by phase."""
    client = get_supabase()
    try:
        query = client.table("api_sync_runs").select("*")
        if phase:
            query = query.eq("phase", phase)
        query = query.order("started_at", desc=True).limit(1)
        result = query.execute()
        return result.data[0] if result.data else None
    except Exception as exc:
        logger.warning("No se pudo leer last_sync_run: %s", exc)
        return None


# ── Usage snapshots ───────────────────────────────────────────────────────────


def save_usage_snapshot(
    rate_state: dict,
    endpoint: str | None = None,
    sync_run_id: int | None = None,
) -> None:
    """Persist a rate-limit header snapshot from the current API state.

    Args:
        rate_state: Dict from client.get_rate_limit_state().
        endpoint: The endpoint that triggered this snapshot (for context).
        sync_run_id: FK to api_sync_runs row (optional).
    """
    if not rate_state.get("requests_limit"):
        return  # No data yet — skip
    client = get_supabase()
    try:
        client.table("api_usage_snapshots").insert(
            {
                "requests_limit": rate_state.get("requests_limit"),
                "requests_remaining": rate_state.get("requests_remaining"),
                "minute_limit": rate_state.get("minute_limit"),
                "minute_remaining": rate_state.get("minute_remaining"),
                "endpoint": endpoint or rate_state.get("last_endpoint"),
                "sync_run_id": sync_run_id,
            }
        ).execute()
    except Exception as exc:
        logger.debug("No se pudo guardar usage_snapshot: %s", exc)


def get_last_usage_snapshot() -> dict | None:
    """Return the most recent usage snapshot row."""
    client = get_supabase()
    try:
        result = (
            client.table("api_usage_snapshots")
            .select("*")
            .order("captured_at", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as exc:
        logger.debug("No se pudo leer last_usage_snapshot: %s", exc)
        return None
