"""DuckDB CRUD for Phase 4: player availability, injuries, and lineups.

All writes are idempotent:
  - fixture_injuries_history  — INSERT OR IGNORE (unique per fixture+team+player+type+reason)
  - fixture_lineups_history   — INSERT OR REPLACE (latest lineup wins)
  - player_availability_signals — INSERT OR IGNORE
  - team_availability_summary — INSERT OR REPLACE (latest summary wins)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import duckdb

logger = logging.getLogger(__name__)

_NOW = lambda: datetime.now(timezone.utc).isoformat()  # noqa: E731


# ── Injuries ──────────────────────────────────────────────────────────────────


def upsert_injury(conn: duckdb.DuckDBPyConnection, injury: dict) -> None:
    """Insert an injury row — skipped if the unique key already exists."""
    conn.execute(
        """
        INSERT OR IGNORE INTO fixture_injuries_history (
            provider_fixture_id, fixture_id, league_id, season,
            team_id, player_id, player_name, type, reason,
            source, fetched_at, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            injury["provider_fixture_id"],
            injury.get("fixture_id"),
            injury.get("league_id"),
            injury.get("season"),
            injury["team_id"],
            injury.get("player_id"),
            injury.get("player_name", ""),
            injury.get("type"),
            injury.get("reason"),
            injury.get("source", "api_football"),
            injury.get("fetched_at") or _NOW(),
            injury.get("raw_json"),
        ],
    )


def get_injuries_for_fixture(
    conn: duckdb.DuckDBPyConnection, provider_fixture_id: int
) -> list[dict]:
    """Return all injury rows for a fixture."""
    rows = conn.execute(
        """
        SELECT team_id, player_id, player_name, type, reason, fetched_at
        FROM fixture_injuries_history
        WHERE provider_fixture_id = ?
        ORDER BY team_id, player_name
        """,
        [provider_fixture_id],
    ).fetchall()
    cols = ["team_id", "player_id", "player_name", "type", "reason", "fetched_at"]
    return [dict(zip(cols, r)) for r in rows]


def get_injuries_for_team(
    conn: duckdb.DuckDBPyConnection,
    team_id: int,
    limit: int = 20,
) -> list[dict]:
    """Return recent injuries for a team (across all fixtures)."""
    rows = conn.execute(
        """
        SELECT provider_fixture_id, player_name, type, reason, fetched_at
        FROM fixture_injuries_history
        WHERE team_id = ?
        ORDER BY fetched_at DESC
        LIMIT ?
        """,
        [team_id, limit],
    ).fetchall()
    cols = ["provider_fixture_id", "player_name", "type", "reason", "fetched_at"]
    return [dict(zip(cols, r)) for r in rows]


# ── Lineups ───────────────────────────────────────────────────────────────────


def upsert_lineup(conn: duckdb.DuckDBPyConnection, lineup: dict) -> None:
    """Insert or replace a lineup row (latest sync wins)."""
    conn.execute(
        """
        INSERT INTO fixture_lineups_history (
            provider_fixture_id, fixture_id, league_id, season,
            team_id, formation, coach_name, fetched_at, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (provider_fixture_id, team_id) DO UPDATE SET
            formation  = excluded.formation,
            coach_name = excluded.coach_name,
            fetched_at = excluded.fetched_at,
            raw_json   = excluded.raw_json
        """,
        [
            lineup["provider_fixture_id"],
            lineup.get("fixture_id"),
            lineup.get("league_id"),
            lineup.get("season"),
            lineup["team_id"],
            lineup.get("formation"),
            lineup.get("coach_name"),
            lineup.get("fetched_at") or _NOW(),
            lineup.get("raw_json"),
        ],
    )


def get_lineups_for_fixture(
    conn: duckdb.DuckDBPyConnection, provider_fixture_id: int
) -> dict[int, dict]:
    """Return {team_id: lineup_dict} for a fixture."""
    rows = conn.execute(
        """
        SELECT team_id, formation, coach_name, fetched_at
        FROM fixture_lineups_history
        WHERE provider_fixture_id = ?
        """,
        [provider_fixture_id],
    ).fetchall()
    result: dict[int, dict] = {}
    for row in rows:
        result[row[0]] = {
            "team_id": row[0],
            "formation": row[1],
            "coach_name": row[2],
            "fetched_at": row[3],
        }
    return result


# ── Availability signals ──────────────────────────────────────────────────────


def upsert_signal(conn: duckdb.DuckDBPyConnection, signal: dict) -> None:
    """Insert a player availability signal — skipped if unique key exists."""
    conn.execute(
        """
        INSERT OR IGNORE INTO player_availability_signals (
            provider_fixture_id, fixture_id, team_id, player_id,
            player_name, signal_type, severity, confidence, reason,
            position, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            signal["provider_fixture_id"],
            signal.get("fixture_id"),
            signal["team_id"],
            signal.get("player_id"),
            signal.get("player_name", ""),
            signal["signal_type"],
            signal.get("severity"),
            signal.get("confidence"),
            signal.get("reason"),
            signal.get("position"),
            signal.get("created_at") or _NOW(),
        ],
    )


def get_signals_for_fixture(
    conn: duckdb.DuckDBPyConnection, provider_fixture_id: int
) -> list[dict]:
    """Return all signals for a fixture."""
    rows = conn.execute(
        """
        SELECT team_id, player_id, player_name, signal_type,
               severity, confidence, reason, position
        FROM player_availability_signals
        WHERE provider_fixture_id = ?
        ORDER BY team_id, signal_type, player_name
        """,
        [provider_fixture_id],
    ).fetchall()
    cols = [
        "team_id", "player_id", "player_name", "signal_type",
        "severity", "confidence", "reason", "position",
    ]
    return [dict(zip(cols, r)) for r in rows]


# ── Team availability summary ─────────────────────────────────────────────────


def upsert_team_summary(conn: duckdb.DuckDBPyConnection, summary: dict) -> None:
    """Insert or replace a team availability summary (latest wins)."""
    conn.execute(
        """
        INSERT INTO team_availability_summary (
            provider_fixture_id, fixture_id, team_id,
            missing_count, suspended_count, injury_count,
            severity_score, impact_label, coverage_status, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (provider_fixture_id, team_id) DO UPDATE SET
            missing_count   = excluded.missing_count,
            suspended_count = excluded.suspended_count,
            injury_count    = excluded.injury_count,
            severity_score  = excluded.severity_score,
            impact_label    = excluded.impact_label,
            coverage_status = excluded.coverage_status,
            updated_at      = excluded.updated_at
        """,
        [
            summary["provider_fixture_id"],
            summary.get("fixture_id"),
            summary["team_id"],
            summary.get("missing_count", 0),
            summary.get("suspended_count", 0),
            summary.get("injury_count", 0),
            summary.get("severity_score", 0.0),
            summary.get("impact_label", "unknown"),
            summary.get("coverage_status", "unknown"),
            summary.get("updated_at") or _NOW(),
        ],
    )


def get_team_summaries_for_fixture(
    conn: duckdb.DuckDBPyConnection, provider_fixture_id: int
) -> dict[int, dict]:
    """Return {team_id: summary_dict} for a fixture."""
    rows = conn.execute(
        """
        SELECT team_id, missing_count, suspended_count, injury_count,
               severity_score, impact_label, coverage_status, updated_at
        FROM team_availability_summary
        WHERE provider_fixture_id = ?
        """,
        [provider_fixture_id],
    ).fetchall()
    cols = [
        "team_id", "missing_count", "suspended_count", "injury_count",
        "severity_score", "impact_label", "coverage_status", "updated_at",
    ]
    result: dict[int, dict] = {}
    for row in rows:
        d = dict(zip(cols, row))
        result[d["team_id"]] = d
    return result


def get_availability_for_fixture(
    conn: duckdb.DuckDBPyConnection,
    provider_fixture_id: int,
) -> dict | None:
    """Return combined availability data for /partido display.

    Returns None if no summary rows exist (no sync done yet).

    Shape:
        {
          "summaries": {team_id: summary_dict, ...},
          "injuries":  [injury_dict, ...],
          "lineups":   {team_id: lineup_dict, ...},
          "signals":   [signal_dict, ...],
        }
    """
    summaries = get_team_summaries_for_fixture(conn, provider_fixture_id)
    if not summaries:
        return None
    return {
        "summaries": summaries,
        "injuries":  get_injuries_for_fixture(conn, provider_fixture_id),
        "lineups":   get_lineups_for_fixture(conn, provider_fixture_id),
        "signals":   get_signals_for_fixture(conn, provider_fixture_id),
    }


# ── Counts ────────────────────────────────────────────────────────────────────


def availability_counts(conn: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Return row counts for all availability tables."""
    counts: dict[str, int] = {}
    for table in (
        "fixture_injuries_history",
        "fixture_lineups_history",
        "player_availability_signals",
        "team_availability_summary",
    ):
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            counts[table] = row[0] if row else 0
        except Exception:
            counts[table] = 0
    return counts
