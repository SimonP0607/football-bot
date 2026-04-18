"""Supabase repository for odds snapshots."""

import logging
from datetime import datetime, timezone

from app.data.repositories.supabase_client import get_supabase

logger = logging.getLogger(__name__)


def upsert_odds_batch(fixture_id: int, rows: list[dict]) -> int:
    """Upsert multiple odds rows for a fixture in one request.

    Args:
        fixture_id: Internal Supabase fixture ID.
        rows: List of dicts with keys ``bookmaker_id``, ``bookmaker_name``,
              ``bet_id``, ``market``, ``selection``, ``odd`` (as returned by
              ``endpoints.fetch_odds``). Older dicts without ``bookmaker_id``
              / ``bet_id`` are still accepted (those fields default to NULL).

    Returns:
        Number of rows upserted.
    """
    if not rows:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    payload = [
        {
            "fixture_id": fixture_id,
            "bookmaker": r.get("bookmaker_name", r.get("bookmaker", "Unknown")),
            "market": r["market"],
            "selection": r["selection"],
            "odd": r["odd"],
            "bookmaker_id": r.get("bookmaker_id"),
            "bet_id": r.get("bet_id"),
            "scope": r.get("scope", "prematch"),
            "last_update": now,
        }
        for r in rows
    ]

    client = get_supabase()
    client.table("odds_snapshots").upsert(
        payload,
        on_conflict="fixture_id,bookmaker,market,selection",
    ).execute()
    logger.debug("upsert_odds_batch fixture_id=%s → %d filas", fixture_id, len(payload))
    return len(payload)


def get_odds_for_fixture(fixture_id: int) -> list[dict]:
    """Return all prematch odds snapshot rows for a fixture."""
    client = get_supabase()
    result = (
        client.table("odds_snapshots")
        .select("*")
        .eq("fixture_id", fixture_id)
        .eq("scope", "prematch")
        .execute()
    )
    return result.data or []


def get_best_odds_for_fixture(
    fixture_id: int, market: str, selection: str
) -> dict | None:
    """Return the single row with the highest odd for a market/selection pair."""
    client = get_supabase()
    result = (
        client.table("odds_snapshots")
        .select("bookmaker, odd, bookmaker_id")
        .eq("fixture_id", fixture_id)
        .eq("market", market)
        .eq("selection", selection)
        .eq("scope", "prematch")
        .order("odd", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def count_odds_today(fixture_ids: list[int]) -> int:
    """Return total odds rows stored for a set of fixture IDs."""
    if not fixture_ids:
        return 0
    client = get_supabase()
    result = (
        client.table("odds_snapshots")
        .select("id", count="exact")
        .in_("fixture_id", fixture_ids)
        .execute()
    )
    return result.count or 0
