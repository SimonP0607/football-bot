"""DuckDB repository for Phase 7 live monitoring tables.

Tables managed here:
  live_fixture_snapshots  — latest known score/status per fixture
  live_fixture_events     — goal/card events
  live_pick_tracking      — per-pick live state (winning/losing/open)
  live_notifications_log  — anti-spam deduplication
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_TABLES = [
    "live_fixture_snapshots",
    "live_fixture_events",
    "live_pick_tracking",
    "live_notifications_log",
]


# ── Snapshots ──────────────────────────────────────────────────────────────────


def upsert_live_snapshot(conn, row: dict) -> None:
    """Insert or update the live snapshot for a fixture."""
    conn.execute(
        """
        INSERT INTO live_fixture_snapshots
            (provider_fixture_id, status_short, status_elapsed,
             goals_home, goals_away, is_finished, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (provider_fixture_id) DO UPDATE SET
            status_short    = excluded.status_short,
            status_elapsed  = excluded.status_elapsed,
            goals_home      = excluded.goals_home,
            goals_away      = excluded.goals_away,
            is_finished     = excluded.is_finished,
            last_seen_at    = excluded.last_seen_at
        """,
        [
            row["provider_fixture_id"],
            row.get("status_short"),
            row.get("status_elapsed"),
            row.get("goals_home"),
            row.get("goals_away"),
            bool(row.get("is_finished", False)),
            row.get("last_seen_at", datetime.now(timezone.utc).isoformat()),
        ],
    )


def get_live_snapshot(conn, prov_fid: int) -> dict | None:
    row = conn.execute(
        """
        SELECT provider_fixture_id, status_short, status_elapsed,
               goals_home, goals_away, is_finished, last_seen_at
        FROM live_fixture_snapshots
        WHERE provider_fixture_id = ?
        """,
        [prov_fid],
    ).fetchone()
    if not row:
        return None
    return {
        "provider_fixture_id": row[0],
        "status_short":        row[1],
        "status_elapsed":      row[2],
        "goals_home":          row[3],
        "goals_away":          row[4],
        "is_finished":         bool(row[5]),
        "last_seen_at":        row[6],
    }


def get_all_live_snapshots(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT provider_fixture_id, status_short, status_elapsed,
               goals_home, goals_away, is_finished, last_seen_at
        FROM live_fixture_snapshots
        ORDER BY provider_fixture_id
        """
    ).fetchall()
    return [
        {
            "provider_fixture_id": r[0],
            "status_short":        r[1],
            "status_elapsed":      r[2],
            "goals_home":          r[3],
            "goals_away":          r[4],
            "is_finished":         bool(r[5]),
            "last_seen_at":        r[6],
        }
        for r in rows
    ]


# ── Events ─────────────────────────────────────────────────────────────────────


def insert_live_event(conn, row: dict) -> None:
    """Insert a live event. Silently ignores duplicate event_ids."""
    conn.execute(
        """
        INSERT OR IGNORE INTO live_fixture_events
            (event_id, provider_fixture_id, event_minute,
             event_type, team_side, player_name, detail)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            row["event_id"],
            row["provider_fixture_id"],
            row.get("event_minute"),
            row.get("event_type"),
            row.get("team_side"),
            row.get("player_name"),
            row.get("detail"),
        ],
    )


def get_events_for_fixture(conn, prov_fid: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT event_id, provider_fixture_id, event_minute,
               event_type, team_side, player_name, detail, captured_at
        FROM live_fixture_events
        WHERE provider_fixture_id = ?
        ORDER BY event_minute
        """,
        [prov_fid],
    ).fetchall()
    return [
        {
            "event_id":            r[0],
            "provider_fixture_id": r[1],
            "event_minute":        r[2],
            "event_type":          r[3],
            "team_side":           r[4],
            "player_name":         r[5],
            "detail":              r[6],
            "captured_at":         r[7],
        }
        for r in rows
    ]


# ── Pick tracking ──────────────────────────────────────────────────────────────


def upsert_pick_tracking(conn, row: dict) -> None:
    """Insert or update the live state of a single pick."""
    conn.execute(
        """
        INSERT INTO live_pick_tracking
            (pick_candidate_id, provider_fixture_id, market_key, selection,
             live_state, goals_home, goals_away, status_elapsed, status_short,
             last_updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (pick_candidate_id) DO UPDATE SET
            live_state      = excluded.live_state,
            goals_home      = excluded.goals_home,
            goals_away      = excluded.goals_away,
            status_elapsed  = excluded.status_elapsed,
            status_short    = excluded.status_short,
            last_updated_at = excluded.last_updated_at
        """,
        [
            row["pick_candidate_id"],
            row["provider_fixture_id"],
            row["market_key"],
            row["selection"],
            row.get("live_state", "open"),
            row.get("goals_home"),
            row.get("goals_away"),
            row.get("status_elapsed"),
            row.get("status_short"),
            datetime.now(timezone.utc).isoformat(),
        ],
    )


def get_pick_tracking(conn, pick_candidate_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT pick_candidate_id, provider_fixture_id, market_key, selection,
               live_state, goals_home, goals_away, status_elapsed, status_short, last_updated_at
        FROM live_pick_tracking
        WHERE pick_candidate_id = ?
        """,
        [pick_candidate_id],
    ).fetchone()
    if not row:
        return None
    return _tracking_row_to_dict(row)


def get_pick_tracking_for_fixture(conn, prov_fid: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT pick_candidate_id, provider_fixture_id, market_key, selection,
               live_state, goals_home, goals_away, status_elapsed, status_short, last_updated_at
        FROM live_pick_tracking
        WHERE provider_fixture_id = ?
        ORDER BY market_key, selection
        """,
        [prov_fid],
    ).fetchall()
    return [_tracking_row_to_dict(r) for r in rows]


def get_all_pick_tracking(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT pick_candidate_id, provider_fixture_id, market_key, selection,
               live_state, goals_home, goals_away, status_elapsed, status_short, last_updated_at
        FROM live_pick_tracking
        ORDER BY provider_fixture_id, market_key
        """
    ).fetchall()
    return [_tracking_row_to_dict(r) for r in rows]


def _tracking_row_to_dict(row: tuple) -> dict:
    return {
        "pick_candidate_id":   row[0],
        "provider_fixture_id": row[1],
        "market_key":          row[2],
        "selection":           row[3],
        "live_state":          row[4],
        "goals_home":          row[5],
        "goals_away":          row[6],
        "status_elapsed":      row[7],
        "status_short":        row[8],
        "last_updated_at":     row[9],
    }


# ── Notifications log ──────────────────────────────────────────────────────────


def notification_already_sent(conn, log_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM live_notifications_log WHERE log_id = ?",
        [log_id],
    ).fetchone()
    return row is not None


def log_notification(conn, row: dict) -> None:
    """Record that a notification was sent. Silently ignores duplicate log_ids."""
    conn.execute(
        """
        INSERT OR IGNORE INTO live_notifications_log
            (log_id, pick_candidate_id, provider_fixture_id,
             notification_type, state)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            row["log_id"],
            row.get("pick_candidate_id"),
            row.get("provider_fixture_id"),
            row.get("notification_type"),
            row.get("state"),
        ],
    )


# ── Counts ─────────────────────────────────────────────────────────────────────


def live_monitor_counts(conn) -> dict[str, int]:
    """Return row counts for all live monitor tables."""
    counts: dict[str, int] = {}
    for table in _TABLES:
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            counts[table] = int(row[0]) if row else 0
        except Exception:
            counts[table] = -1
    return counts
