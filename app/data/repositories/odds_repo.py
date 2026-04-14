"""Supabase repository for odds snapshots."""

import logging
from app.data.repositories.supabase_client import get_supabase

logger = logging.getLogger(__name__)


def upsert_odd(
    fixture_id: int,
    bookmaker: str,
    market: str,
    selection: str,
    odd: float,
) -> None:
    """Insert or update a single odds row.

    The unique key is (fixture_id, bookmaker, market, selection). If a row
    already exists the ``odd`` value and ``captured_at`` are updated.
    """
    client = get_supabase()
    client.table("odds_snapshots").upsert(
        {
            "fixture_id": fixture_id,
            "bookmaker": bookmaker,
            "market": market,
            "selection": selection,
            "odd": odd,
        },
        on_conflict="fixture_id,bookmaker,market,selection",
    ).execute()


def upsert_odds_batch(fixture_id: int, rows: list[dict]) -> int:
    """Upsert multiple odds rows for a fixture in one request.

    Args:
        fixture_id: Internal Supabase fixture ID.
        rows: List of dicts with keys ``bookmaker``, ``market``, ``selection``,
              ``odd`` (as returned by ``endpoints.fetch_odds``).

    Returns:
        Number of rows upserted.
    """
    if not rows:
        return 0

    payload = [
        {
            "fixture_id": fixture_id,
            "bookmaker": r["bookmaker"],
            "market": r["market"],
            "selection": r["selection"],
            "odd": r["odd"],
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
    """Return all odds snapshot rows for a fixture."""
    client = get_supabase()
    result = (
        client.table("odds_snapshots")
        .select("*")
        .eq("fixture_id", fixture_id)
        .execute()
    )
    return result.data or []


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
