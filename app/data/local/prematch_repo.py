"""Phase 6: Prematch Intelligence — DuckDB read/write repository.

All writes are idempotent (ON CONFLICT DO UPDATE or INSERT OR IGNORE).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Odds movement ─────────────────────────────────────────────────────────────


def upsert_odds_movement(conn, row: dict) -> None:
    """Upsert one odds movement row.

    On first insert: opening_odds = current_odds (first capture).
    On conflict: update current_odds, best_odds, delta fields, captured_at.
    opening_odds is preserved from the first insert.
    """
    prov_fid   = row["provider_fixture_id"]
    market_key = row["market_key"]
    selection  = row["selection"]
    bookmaker  = row.get("bookmaker") or ""
    current    = float(row["current_odds"])
    best       = row.get("best_odds")
    now        = row.get("captured_at") or _now()
    source     = row.get("source", "supabase")
    fixture_id = row.get("fixture_id")
    league_id  = row.get("league_id")

    implied_current = round(1.0 / current, 6) if current > 1.0 else None

    existing = conn.execute(
        """
        SELECT opening_odds, implied_opening
        FROM prematch_odds_movement
        WHERE provider_fixture_id = ? AND market_key = ? AND selection = ? AND bookmaker = ?
        """,
        [prov_fid, market_key, selection, bookmaker],
    ).fetchone()

    if existing is None:
        opening          = current
        implied_opening  = implied_current
        odds_delta       = 0.0
        implied_delta    = 0.0
        direction        = "stable"
        strength         = "none"
    else:
        opening         = existing[0]
        implied_opening = existing[1]
        odds_delta      = round(current - opening, 6)
        if implied_opening is not None and implied_current is not None:
            implied_delta = round(implied_current - implied_opening, 6)
        else:
            implied_delta = 0.0
        direction = _movement_direction(odds_delta)
        strength  = _movement_strength(implied_delta)

    conn.execute(
        """
        INSERT INTO prematch_odds_movement
            (fixture_id, provider_fixture_id, league_id, market_key, selection,
             bookmaker, opening_odds, current_odds, best_odds,
             implied_opening, implied_current, odds_delta, implied_delta,
             movement_direction, movement_strength, captured_at, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (provider_fixture_id, market_key, selection, bookmaker)
        DO UPDATE SET
            current_odds       = excluded.current_odds,
            best_odds          = excluded.best_odds,
            implied_current    = excluded.implied_current,
            odds_delta         = excluded.odds_delta,
            implied_delta      = excluded.implied_delta,
            movement_direction = excluded.movement_direction,
            movement_strength  = excluded.movement_strength,
            captured_at        = excluded.captured_at,
            source             = excluded.source
        """,
        [
            fixture_id, prov_fid, league_id, market_key, selection,
            bookmaker, opening, current, best,
            implied_opening, implied_current, odds_delta, implied_delta,
            direction, strength, now, source,
        ],
    )


def get_odds_movement_for_fixture(conn, fixture_id: int) -> list[dict]:
    """Return all odds movement rows for a fixture (by Supabase fixture_id)."""
    rows = conn.execute(
        """
        SELECT market_key, selection, bookmaker, opening_odds, current_odds,
               best_odds, odds_delta, implied_delta, movement_direction, movement_strength,
               captured_at
        FROM prematch_odds_movement
        WHERE fixture_id = ?
        ORDER BY market_key, selection
        """,
        [fixture_id],
    ).fetchall()
    cols = [
        "market_key", "selection", "bookmaker", "opening_odds", "current_odds",
        "best_odds", "odds_delta", "implied_delta", "movement_direction", "movement_strength",
        "captured_at",
    ]
    return [dict(zip(cols, r)) for r in rows]


def get_odds_movement_by_provider_fixture(conn, provider_fixture_id: int) -> list[dict]:
    """Return all odds movement rows for a fixture (by provider_fixture_id)."""
    rows = conn.execute(
        """
        SELECT market_key, selection, bookmaker, opening_odds, current_odds,
               best_odds, odds_delta, implied_delta, movement_direction, movement_strength,
               captured_at
        FROM prematch_odds_movement
        WHERE provider_fixture_id = ?
        ORDER BY market_key, selection
        """,
        [provider_fixture_id],
    ).fetchall()
    cols = [
        "market_key", "selection", "bookmaker", "opening_odds", "current_odds",
        "best_odds", "odds_delta", "implied_delta", "movement_direction", "movement_strength",
        "captured_at",
    ]
    return [dict(zip(cols, r)) for r in rows]


# ── Alerts ────────────────────────────────────────────────────────────────────


def upsert_fixture_alert(conn, row: dict) -> None:
    """Upsert a fixture alert. Updates message/severity if the alert already exists."""
    prov_fid   = row["provider_fixture_id"]
    alert_type = row["alert_type"]
    market     = row.get("related_market") or ""
    selection  = row.get("related_selection") or ""
    now        = row.get("created_at") or _now()
    meta       = row.get("metadata_json")
    if isinstance(meta, dict):
        meta = json.dumps(meta)

    conn.execute(
        """
        INSERT INTO prematch_fixture_alerts
            (fixture_id, provider_fixture_id, league_id, alert_type, severity,
             title, message, related_market, related_selection, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (provider_fixture_id, alert_type, related_market, related_selection)
        DO UPDATE SET
            severity      = excluded.severity,
            title         = excluded.title,
            message       = excluded.message,
            metadata_json = excluded.metadata_json,
            created_at    = excluded.created_at
        """,
        [
            row.get("fixture_id"), prov_fid, row.get("league_id"),
            alert_type, row.get("severity", "low"),
            row["title"], row.get("message"),
            market, selection, meta, now,
        ],
    )


def get_recent_alerts(conn, days: int = 1) -> list[dict]:
    """Return alerts created within the last N days."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """
        SELECT provider_fixture_id, fixture_id, alert_type, severity,
               title, message, related_market, related_selection, created_at
        FROM prematch_fixture_alerts
        WHERE created_at >= ?
        ORDER BY severity DESC, created_at DESC
        """,
        [cutoff],
    ).fetchall()
    cols = [
        "provider_fixture_id", "fixture_id", "alert_type", "severity",
        "title", "message", "related_market", "related_selection", "created_at",
    ]
    return [dict(zip(cols, r)) for r in rows]


def get_alerts_for_fixture(conn, provider_fixture_id: int) -> list[dict]:
    """Return all alerts for a specific fixture."""
    rows = conn.execute(
        """
        SELECT alert_type, severity, title, message,
               related_market, related_selection, created_at
        FROM prematch_fixture_alerts
        WHERE provider_fixture_id = ?
        ORDER BY severity DESC, created_at DESC
        """,
        [provider_fixture_id],
    ).fetchall()
    cols = [
        "alert_type", "severity", "title", "message",
        "related_market", "related_selection", "created_at",
    ]
    return [dict(zip(cols, r)) for r in rows]


# ── Lineup status ─────────────────────────────────────────────────────────────


def upsert_lineup_status(conn, row: dict) -> None:
    """Upsert lineup status for a fixture. ON CONFLICT always updates."""
    prov_fid = row["provider_fixture_id"]
    now      = row.get("captured_at") or _now()
    meta     = row.get("metadata_json")
    if isinstance(meta, dict):
        meta = json.dumps(meta)

    conn.execute(
        """
        INSERT INTO prematch_lineup_status
            (fixture_id, provider_fixture_id, home_team_id, away_team_id,
             home_formation, away_formation, lineups_available, lineups_confirmed,
             home_missing_count, away_missing_count, home_impact, away_impact,
             captured_at, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (provider_fixture_id)
        DO UPDATE SET
            home_formation     = excluded.home_formation,
            away_formation     = excluded.away_formation,
            lineups_available  = excluded.lineups_available,
            lineups_confirmed  = excluded.lineups_confirmed,
            home_missing_count = excluded.home_missing_count,
            away_missing_count = excluded.away_missing_count,
            home_impact        = excluded.home_impact,
            away_impact        = excluded.away_impact,
            captured_at        = excluded.captured_at,
            metadata_json      = excluded.metadata_json
        """,
        [
            row.get("fixture_id"), prov_fid,
            row.get("home_team_id"), row.get("away_team_id"),
            row.get("home_formation"), row.get("away_formation"),
            bool(row.get("lineups_available", False)),
            bool(row.get("lineups_confirmed", False)),
            int(row.get("home_missing_count", 0)),
            int(row.get("away_missing_count", 0)),
            row.get("home_impact", "unknown"),
            row.get("away_impact", "unknown"),
            now, meta,
        ],
    )


def get_lineup_status_for_fixture(conn, provider_fixture_id: int) -> dict | None:
    """Return lineup status row for a fixture, or None."""
    row = conn.execute(
        """
        SELECT fixture_id, provider_fixture_id, home_team_id, away_team_id,
               home_formation, away_formation, lineups_available, lineups_confirmed,
               home_missing_count, away_missing_count, home_impact, away_impact,
               captured_at, metadata_json
        FROM prematch_lineup_status
        WHERE provider_fixture_id = ?
        """,
        [provider_fixture_id],
    ).fetchone()
    if row is None:
        return None
    cols = [
        "fixture_id", "provider_fixture_id", "home_team_id", "away_team_id",
        "home_formation", "away_formation", "lineups_available", "lineups_confirmed",
        "home_missing_count", "away_missing_count", "home_impact", "away_impact",
        "captured_at", "metadata_json",
    ]
    return dict(zip(cols, row))


# ── Aggregate summary ─────────────────────────────────────────────────────────


def get_prematch_summary_for_fixture(conn, provider_fixture_id: int) -> dict | None:
    """Return combined prematch intelligence for a fixture.

    Returns None if no data exists at all.
    """
    odds_rows  = get_odds_movement_by_provider_fixture(conn, provider_fixture_id)
    alert_rows = get_alerts_for_fixture(conn, provider_fixture_id)
    lineup     = get_lineup_status_for_fixture(conn, provider_fixture_id)

    if not odds_rows and not alert_rows and lineup is None:
        return None

    return {
        "odds_movement": odds_rows,
        "alerts":        alert_rows,
        "lineup":        lineup,
    }


# ── Table counts ──────────────────────────────────────────────────────────────


def prematch_counts(conn) -> dict[str, int]:
    """Return row counts for all prematch tables."""
    result = {}
    for table in (
        "prematch_odds_movement",
        "prematch_fixture_alerts",
        "prematch_lineup_status",
    ):
        try:
            result[table] = conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
        except Exception:
            result[table] = -1
    return result


# ── Internal helpers ──────────────────────────────────────────────────────────


def _movement_direction(odds_delta: float) -> str:
    if odds_delta < -0.001:
        return "shortening"
    if odds_delta > 0.001:
        return "drifting"
    return "stable"


def _movement_strength(implied_delta: float) -> str:
    abs_delta = abs(implied_delta)
    if abs_delta >= 0.05:
        return "high"
    if abs_delta >= 0.025:
        return "medium"
    if abs_delta >= 0.01:
        return "low"
    return "none"
