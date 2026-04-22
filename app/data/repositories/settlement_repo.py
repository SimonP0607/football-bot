"""Supabase repository for pick_results (settlement / ROI tracking).

Table: pick_results  (migration 012_settlement.sql)
  Tracks whether each published pick was a win, loss, or void.
  One row per pick_candidate (UNIQUE constraint on pick_candidate_id).
"""

import logging
from datetime import datetime, timezone

from app.data.repositories.supabase_client import get_supabase

logger = logging.getLogger(__name__)


def create_pending(
    pick_candidate_id: int,
    fixture_id: int,
    market_key: str,
    selection: str,
    odd_taken: float,
) -> dict:
    """Insert a pending result row for a newly published pick.

    Uses upsert so re-running the pipeline for the same pick is idempotent —
    an existing 'win'/'loss' row is never overwritten because the UNIQUE
    constraint on pick_candidate_id causes Supabase to skip the upsert when
    the row already exists (on_conflict with no update columns).

    Returns the stored row dict (empty dict if the row already existed).
    """
    client = get_supabase()
    try:
        result = (
            client.table("pick_results")
            .upsert(
                {
                    "pick_candidate_id": pick_candidate_id,
                    "fixture_id": fixture_id,
                    "market_key": market_key,
                    "selection": selection,
                    "odd_taken": round(float(odd_taken), 2),
                    "result_status": "pending",
                },
                on_conflict="pick_candidate_id",
                ignore_duplicates=True,
            )
            .execute()
        )
        row = result.data[0] if result.data else {}
        if row:
            logger.debug(
                "settlement create_pending pick_candidate_id=%s fixture=%s %s/%s odd=%.2f",
                pick_candidate_id, fixture_id, market_key, selection, odd_taken,
            )
        return row
    except Exception as exc:
        logger.warning(
            "settlement create_pending falló pick_candidate_id=%s: %s",
            pick_candidate_id, exc,
        )
        return {}


def settle(
    pick_candidate_id: int,
    result_status: str,
    profit_units: float,
) -> dict:
    """Mark a pending pick_result as settled (win / loss / void).

    Args:
        pick_candidate_id: FK to pick_candidates.id.
        result_status: 'win', 'loss', or 'void'.
        profit_units: +odd-1 for win, -1 for loss, 0 for void.

    Returns the updated row dict.
    """
    if result_status not in ("win", "loss", "void"):
        raise ValueError(f"result_status inválido: {result_status!r}")

    client = get_supabase()
    result = (
        client.table("pick_results")
        .update(
            {
                "result_status": result_status,
                "profit_units": round(profit_units, 4),
                "settled_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        .eq("pick_candidate_id", pick_candidate_id)
        .execute()
    )
    row = result.data[0] if result.data else {}
    logger.info(
        "settlement settled pick_candidate_id=%s → %s (%.4f u)",
        pick_candidate_id, result_status, profit_units,
    )
    return row


def get_pending() -> list[dict]:
    """Return all pick_results with result_status='pending', ordered oldest first."""
    client = get_supabase()
    result = (
        client.table("pick_results")
        .select("*")
        .eq("result_status", "pending")
        .order("created_at", desc=False)
        .execute()
    )
    return result.data or []


def get_roi_summary() -> dict:
    """Return win/loss/void counts and total profit_units across all settled picks."""
    client = get_supabase()
    rows = (
        client.table("pick_results")
        .select("result_status, profit_units")
        .neq("result_status", "pending")
        .execute()
    ).data or []

    wins = sum(1 for r in rows if r["result_status"] == "win")
    losses = sum(1 for r in rows if r["result_status"] == "loss")
    voids = sum(1 for r in rows if r["result_status"] == "void")
    total_profit = sum(
        float(r["profit_units"] or 0)
        for r in rows
        if r["result_status"] in ("win", "loss")
    )
    settled = wins + losses
    roi = (total_profit / settled * 100) if settled else 0.0

    return {
        "wins": wins,
        "losses": losses,
        "voids": voids,
        "settled": settled,
        "total_profit_units": round(total_profit, 4),
        "roi_pct": round(roi, 2),
    }
