"""Supabase repository for pick candidates (replaces predictions table).

Table: pick_candidates
  - market_key: canonical market code ('1X2', 'OU25', 'BTTS')
  - selection:  selected outcome ('Home', 'Over 2.5', 'Yes', etc.)
  - Replaces old: market (same values), recommended_pick (renamed to selection)
"""

import logging

from app.data.repositories.supabase_client import get_supabase

logger = logging.getLogger(__name__)


def save_prediction(
    fixture_id: int,
    market: str,
    selection: str,
    model_probability: float,
    implied_probability: float,
    edge: float,
    confidence_score: float,
    argument_json: dict,
    is_publishable: bool,
) -> dict:
    """Insert or update a pick candidate (unique per fixture + market_key + selection).

    Args:
        market: Canonical market code ('1X2', 'OU25', 'BTTS').
        selection: Selected outcome ('Home', 'Draw', 'Away', 'Over 2.5', etc.).

    Returns the stored row dict.
    """
    client = get_supabase()
    result = (
        client.table("pick_candidates")
        .upsert(
            {
                "fixture_id": fixture_id,
                "market_key": market,
                "selection": selection,
                "model_probability": model_probability,
                "implied_probability": implied_probability,
                "edge": edge,
                "confidence_score": confidence_score,
                "argument_json": argument_json,
                "is_publishable": is_publishable,
            },
            on_conflict="fixture_id,market_key,selection",
        )
        .execute()
    )
    row = result.data[0] if result.data else {}
    logger.debug(
        "save_prediction fixture=%s market=%s selection=%s edge=%.4f publishable=%s",
        fixture_id, market, selection, edge, is_publishable,
    )
    return row


def get_publishable_today(fixture_ids: list[int]) -> list[dict]:
    """Return publishable pick candidates for today's fixtures, ordered by edge desc."""
    if not fixture_ids:
        return []
    client = get_supabase()
    result = (
        client.table("pick_candidates")
        .select("*")
        .eq("is_publishable", True)
        .in_("fixture_id", fixture_ids)
        .order("edge", desc=True)
        .execute()
    )
    return result.data or []


def get_top_picks(fixture_ids: list[int], limit: int = 5) -> list[dict]:
    """Return top pick candidates by confidence_score for today's fixtures."""
    if not fixture_ids:
        return []
    client = get_supabase()
    result = (
        client.table("pick_candidates")
        .select("*")
        .eq("is_publishable", True)
        .in_("fixture_id", fixture_ids)
        .order("confidence_score", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


def count_predictions_today(fixture_ids: list[int]) -> int:
    """Return count of publishable pick candidates for today's fixtures."""
    if not fixture_ids:
        return 0
    client = get_supabase()
    result = (
        client.table("pick_candidates")
        .select("id", count="exact")
        .eq("is_publishable", True)
        .in_("fixture_id", fixture_ids)
        .execute()
    )
    return result.count or 0
