"""Supabase repository for reference tables: ref_bookmakers and ref_bet_types.

These tables are populated during Phase A (reference sync) and read during
odds processing to enrich odds_snapshots with numeric IDs.
"""

import logging
from app.data.repositories.supabase_client import get_supabase

logger = logging.getLogger(__name__)


# ── Bookmakers ────────────────────────────────────────────────────────────────


def upsert_bookmakers(bookmakers: list[dict]) -> int:
    """Upsert a list of bookmaker dicts from /odds/bookmakers.

    Args:
        bookmakers: List of {"id": int, "name": str} from API-Football.

    Returns:
        Number of rows upserted.
    """
    if not bookmakers:
        return 0

    payload = [
        {"provider_bookmaker_id": bk["id"], "name": bk["name"]}
        for bk in bookmakers
        if bk.get("id") and bk.get("name")
    ]
    if not payload:
        return 0

    client = get_supabase()
    client.table("ref_bookmakers").upsert(
        payload, on_conflict="provider_bookmaker_id"
    ).execute()
    logger.info("upsert_bookmakers → %d bookmakers guardados", len(payload))
    return len(payload)


def get_bookmaker_id_map() -> dict[str, int]:
    """Return {bookmaker_name: provider_bookmaker_id} for all stored bookmakers."""
    client = get_supabase()
    result = client.table("ref_bookmakers").select("provider_bookmaker_id, name").execute()
    return {row["name"]: row["provider_bookmaker_id"] for row in (result.data or [])}


# ── Bet types ─────────────────────────────────────────────────────────────────


def upsert_bet_types(bet_types: list[dict], scope: str = "prematch") -> int:
    """Upsert a list of bet type dicts from /odds/bets.

    Args:
        bet_types: List of {"id": int, "name": str} from API-Football.
        scope: "prematch" or "live". Never mix /odds/bets with /odds/live/bets.

    Returns:
        Number of rows upserted.
    """
    if not bet_types:
        return 0

    payload = [
        {"provider_bet_id": bt["id"], "name": bt["name"], "scope": scope}
        for bt in bet_types
        if bt.get("id") and bt.get("name")
    ]
    if not payload:
        return 0

    client = get_supabase()
    client.table("ref_bet_types").upsert(
        payload, on_conflict="provider_bet_id"
    ).execute()
    logger.info("upsert_bet_types → %d tipos guardados (scope=%s)", len(payload), scope)
    return len(payload)


def get_bet_id_map(scope: str = "prematch") -> dict[str, int]:
    """Return {bet_name: provider_bet_id} for stored bet types of the given scope."""
    client = get_supabase()
    result = (
        client.table("ref_bet_types")
        .select("provider_bet_id, name")
        .eq("scope", scope)
        .execute()
    )
    return {row["name"]: row["provider_bet_id"] for row in (result.data or [])}
