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


def get_candidate_id(fixture_id: int, market: str, selection: str) -> int | None:
    """Return the internal id of a pick_candidate row, or None if not found."""
    client = get_supabase()
    result = (
        client.table("pick_candidates")
        .select("id")
        .eq("fixture_id", fixture_id)
        .eq("market_key", market)
        .eq("selection", selection)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]["id"]
    return None


def update_value_metrics(fixture_id: int, market: str, selection: str, value_result: dict) -> None:
    """Update value engine columns on an existing pick_candidate row.

    Requires migration 014_value_engine_candidates.sql to have been applied.
    Safe to call without migration (Supabase will reject unknown columns; caller
    should catch any exception and log it rather than crashing).
    """
    from datetime import datetime, timezone
    import json as _json
    client = get_supabase()
    avail_coverage = value_result.get("availability_coverage")
    avail_sub: dict | None = (
        {
            "coverage":        avail_coverage,
            "modeled_impact":  value_result.get("availability_modeled_impact"),
            "opponent_impact": value_result.get("availability_opponent_impact"),
            "penalty":         value_result.get("availability_penalty"),
            "boost":           value_result.get("availability_boost"),
            "quality_before":  value_result.get("quality_before_availability"),
            "warning":         value_result.get("availability_warning"),
        }
        if avail_coverage is not None
        else None
    )
    meta_blob = {
        "model_scope":            value_result.get("model_scope"),
        "calibrator_scope":       value_result.get("calibrator_scope"),
        "historical_sample_size": value_result.get("historical_sample_size"),
        "value_rank_score":       value_result.get("value_rank_score"),
        "availability":           avail_sub,
        "prematch":               value_result.get("prematch_signal"),
    }
    payload = {
        "value_engine_status":     value_result.get("value_engine_status"),
        "value_p_cal":             value_result.get("p_cal"),
        "value_fair_odds":         value_result.get("fair_odds"),
        "value_p_mkt":             value_result.get("p_mkt"),
        "value_edge":              value_result.get("edge"),
        "value_ev":                value_result.get("ev"),
        "value_ev_adj":            value_result.get("ev_adj"),
        "value_w_rel":             value_result.get("w_rel"),
        "value_quality_score":     value_result.get("quality_score"),
        "value_rejection_reason":  value_result.get("rejection_reason"),
        "value_engine_meta":       meta_blob,
        "value_engine_created_at": datetime.now(timezone.utc).isoformat(),
    }
    (
        client.table("pick_candidates")
        .update(payload)
        .eq("fixture_id", fixture_id)
        .eq("market_key", market)
        .eq("selection", selection)
        .execute()
    )
    logger.debug(
        "update_value_metrics fixture=%s %s/%s status=%s",
        fixture_id, market, selection, value_result.get("value_engine_status"),
    )


def get_candidates_for_fixtures(fixture_ids: list[int]) -> list[dict]:
    """Return all pick_candidate rows for a set of fixture IDs."""
    if not fixture_ids:
        return []
    client = get_supabase()
    result = (
        client.table("pick_candidates")
        .select("*")
        .in_("fixture_id", fixture_ids)
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
