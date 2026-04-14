"""Supabase repository for predictions."""

import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.data.repositories.supabase_client import get_supabase
from app.core.config import settings

logger = logging.getLogger(__name__)


def save_prediction(
    fixture_id: int,
    market: str,
    recommended_pick: str,
    model_probability: float,
    implied_probability: float,
    edge: float,
    confidence_score: float,
    argument_json: dict,
    is_publishable: bool,
) -> dict:
    """Insert or update a prediction (unique per fixture + market).

    Returns the stored row dict.
    """
    client = get_supabase()
    result = (
        client.table("predictions")
        .upsert(
            {
                "fixture_id": fixture_id,
                "market": market,
                "recommended_pick": recommended_pick,
                "model_probability": model_probability,
                "implied_probability": implied_probability,
                "edge": edge,
                "confidence_score": confidence_score,
                "argument_json": argument_json,
                "is_publishable": is_publishable,
            },
            on_conflict="fixture_id,market",
        )
        .execute()
    )
    row = result.data[0] if result.data else {}
    logger.debug(
        "save_prediction fixture=%s market=%s edge=%.4f publishable=%s",
        fixture_id, market, edge, is_publishable,
    )
    return row


def get_publishable_today(fixture_ids: list[int]) -> list[dict]:
    """Return publishable predictions for today's fixtures, ordered by edge desc.

    Args:
        fixture_ids: Internal IDs of today's fixtures (from ``fixture_repo``).
    """
    if not fixture_ids:
        return []
    client = get_supabase()
    result = (
        client.table("predictions")
        .select("*")
        .eq("is_publishable", True)
        .in_("fixture_id", fixture_ids)
        .order("edge", desc=True)
        .execute()
    )
    return result.data or []


def get_top_picks(fixture_ids: list[int], limit: int = 5) -> list[dict]:
    """Return top predictions by confidence_score for today's fixtures."""
    if not fixture_ids:
        return []
    client = get_supabase()
    result = (
        client.table("predictions")
        .select("*")
        .eq("is_publishable", True)
        .in_("fixture_id", fixture_ids)
        .order("confidence_score", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


def count_predictions_today(fixture_ids: list[int]) -> int:
    """Return count of publishable predictions for today's fixtures."""
    if not fixture_ids:
        return 0
    client = get_supabase()
    result = (
        client.table("predictions")
        .select("id", count="exact")
        .eq("is_publishable", True)
        .in_("fixture_id", fixture_ids)
        .execute()
    )
    return result.count or 0
