"""Supabase repository for odds snapshots."""

import logging
from datetime import datetime, timezone

from app.data.repositories.supabase_client import get_supabase

logger = logging.getLogger(__name__)


def upsert_odds_batch(fixture_id: int, rows: list[dict]) -> int:
    """Upsert multiple odds rows for a fixture in one request.

    Args:
        fixture_id: Internal Supabase fixture ID.
        rows: List of dicts as returned by ``endpoints.fetch_odds()``.
              Required keys: ``bookmaker_name`` (or ``bookmaker``), ``market``
              (canonical code: '1X2', 'OU25', 'BTTS'), ``selection``, ``odd``.
              Optional: ``bookmaker_id``, ``bet_id``, ``scope``.

    Returns:
        Number of rows upserted.
    """
    if not rows:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    payload = [
        {
            "fixture_id": fixture_id,
            "bookmaker_name": r.get("bookmaker_name", r.get("bookmaker", "Unknown")),
            "bookmaker_id": r.get("bookmaker_id"),
            "bet_id": r.get("bet_id"),
            "market_key": r["market"],   # endpoints.fetch_odds returns canonical code
            "selection": r["selection"],
            "odd": r["odd"],
            "scope": r.get("scope", "prematch"),
            "last_update": now,
        }
        for r in rows
    ]

    client = get_supabase()
    client.table("odds_snapshots").upsert(
        payload,
        on_conflict="fixture_id,bookmaker_name,market_key,selection,scope",
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
    fixture_id: int, market_key: str, selection: str
) -> dict | None:
    """Return the single row with the highest odd for a market_key/selection pair."""
    client = get_supabase()
    result = (
        client.table("odds_snapshots")
        .select("bookmaker_name, odd, bookmaker_id")
        .eq("fixture_id", fixture_id)
        .eq("market_key", market_key)
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
