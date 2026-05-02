"""DuckDB CRUD for the entity catalog (teams, players, squads).

All writes use INSERT OR REPLACE / INSERT OR IGNORE so the operations are
idempotent and safe to run repeatedly.

team_identity.provider_team_id == the team_id used in fixtures_history,
standings_history, and team_elo_history, so joins work across all local tables.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import duckdb

logger = logging.getLogger(__name__)

_NOW = lambda: datetime.now(timezone.utc).isoformat()  # noqa: E731


# ── Team identity ─────────────────────────────────────────────────────────────


def upsert_team_identity(conn: duckdb.DuckDBPyConnection, team: dict) -> None:
    """Insert or replace a team in team_identity.

    Required key: provider_team_id, name.
    Optional: code, country, is_national, logo, founded, venue_id,
              venue_name, venue_city, venue_capacity.
    """
    conn.execute(
        """
        INSERT INTO team_identity (
            provider_team_id, name, code, country, is_national,
            logo, founded, venue_id, venue_name, venue_city, venue_capacity,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (provider_team_id) DO UPDATE SET
            name         = excluded.name,
            code         = COALESCE(excluded.code,         team_identity.code),
            country      = COALESCE(excluded.country,      team_identity.country),
            is_national  = excluded.is_national,
            logo         = COALESCE(excluded.logo,         team_identity.logo),
            founded      = COALESCE(excluded.founded,      team_identity.founded),
            venue_id     = COALESCE(excluded.venue_id,     team_identity.venue_id),
            venue_name   = COALESCE(excluded.venue_name,   team_identity.venue_name),
            venue_city   = COALESCE(excluded.venue_city,   team_identity.venue_city),
            venue_capacity = COALESCE(excluded.venue_capacity, team_identity.venue_capacity),
            updated_at   = excluded.updated_at
        """,
        [
            team["provider_team_id"],
            team.get("name", ""),
            team.get("code"),
            team.get("country"),
            bool(team.get("is_national", False)),
            team.get("logo"),
            team.get("founded"),
            team.get("venue_id"),
            team.get("venue_name"),
            team.get("venue_city"),
            team.get("venue_capacity"),
            _NOW(),
            _NOW(),
        ],
    )


def upsert_team_membership(
    conn: duckdb.DuckDBPyConnection,
    provider_team_id: int,
    provider_league_id: int,
    season: int,
    *,
    competition_type: str | None = None,
    scope: str = "club",
    country: str | None = None,
    source: str = "api",
) -> None:
    """Insert a team-league-season membership (ignored if already exists)."""
    conn.execute(
        """
        INSERT OR IGNORE INTO team_season_membership (
            provider_team_id, provider_league_id, season,
            competition_type, scope, country, source, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            provider_team_id, provider_league_id, season,
            competition_type, scope, country, source,
            _NOW(), _NOW(),
        ],
    )


# ── Player identity ───────────────────────────────────────────────────────────


def upsert_player_identity(conn: duckdb.DuckDBPyConnection, player: dict) -> None:
    """Insert or replace a player in player_identity.

    Required key: provider_player_id, name.
    Optional: firstname, lastname, age, birth_date, birth_place, birth_country,
              nationality, height, weight, injured, photo.
    """
    conn.execute(
        """
        INSERT INTO player_identity (
            provider_player_id, name, firstname, lastname,
            age, birth_date, birth_place, birth_country,
            nationality, height, weight, injured, photo,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (provider_player_id) DO UPDATE SET
            name         = excluded.name,
            firstname    = COALESCE(excluded.firstname,    player_identity.firstname),
            lastname     = COALESCE(excluded.lastname,     player_identity.lastname),
            age          = COALESCE(excluded.age,          player_identity.age),
            birth_date   = COALESCE(excluded.birth_date,   player_identity.birth_date),
            birth_place  = COALESCE(excluded.birth_place,  player_identity.birth_place),
            birth_country = COALESCE(excluded.birth_country, player_identity.birth_country),
            nationality  = COALESCE(excluded.nationality,  player_identity.nationality),
            height       = COALESCE(excluded.height,       player_identity.height),
            weight       = COALESCE(excluded.weight,       player_identity.weight),
            injured      = excluded.injured,
            photo        = COALESCE(excluded.photo,        player_identity.photo),
            updated_at   = excluded.updated_at
        """,
        [
            player["provider_player_id"],
            player.get("name", ""),
            player.get("firstname"),
            player.get("lastname"),
            player.get("age"),
            player.get("birth_date"),
            player.get("birth_place"),
            player.get("birth_country"),
            player.get("nationality"),
            player.get("height"),
            player.get("weight"),
            bool(player.get("injured", False)),
            player.get("photo"),
            _NOW(),
            _NOW(),
        ],
    )


def upsert_squad_membership(
    conn: duckdb.DuckDBPyConnection,
    provider_player_id: int,
    provider_team_id: int,
    season: int | None,
    provider_league_id: int | None = None,
    *,
    position: str | None = None,
    number: int | None = None,
    source: str = "squads_endpoint",
) -> None:
    """Insert a squad membership row (ignored if already exists)."""
    conn.execute(
        """
        INSERT OR IGNORE INTO squad_membership (
            provider_player_id, provider_team_id, season, provider_league_id,
            position, number, source, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            provider_player_id, provider_team_id, season, provider_league_id,
            position, number, source,
            _NOW(), _NOW(),
        ],
    )


# ── Reads ─────────────────────────────────────────────────────────────────────


def get_teams_for_league_season(
    conn: duckdb.DuckDBPyConnection, provider_league_id: int, season: int
) -> list[dict]:
    """Return all teams that played in a league/season."""
    rows = conn.execute(
        """
        SELECT t.provider_team_id, t.name, t.country, t.is_national,
               t.code, t.founded, t.venue_name, t.venue_city,
               m.scope, m.competition_type
        FROM team_season_membership m
        JOIN team_identity t ON t.provider_team_id = m.provider_team_id
        WHERE m.provider_league_id = ? AND m.season = ?
        ORDER BY t.name
        """,
        [provider_league_id, season],
    ).fetchall()
    cols = [
        "provider_team_id", "name", "country", "is_national",
        "code", "founded", "venue_name", "venue_city",
        "scope", "competition_type",
    ]
    return [dict(zip(cols, r)) for r in rows]


def get_squad_for_team(
    conn: duckdb.DuckDBPyConnection,
    provider_team_id: int,
    season: int | None = None,
) -> list[dict]:
    """Return squad members for a team (optionally filtered by season)."""
    base = """
        SELECT p.provider_player_id, p.name, p.nationality, p.age,
               s.position, s.number, s.season, s.provider_league_id, s.source
        FROM squad_membership s
        JOIN player_identity p ON p.provider_player_id = s.provider_player_id
        WHERE s.provider_team_id = ?
    """
    params: list[Any] = [provider_team_id]
    if season is not None:
        base += " AND s.season = ?"
        params.append(season)
    base += " ORDER BY s.position, p.name"
    rows = conn.execute(base, params).fetchall()
    cols = [
        "provider_player_id", "name", "nationality", "age",
        "position", "number", "season", "provider_league_id", "source",
    ]
    return [dict(zip(cols, r)) for r in rows]


def find_team(conn: duckdb.DuckDBPyConnection, query: str, limit: int = 10) -> list[dict]:
    """Search teams by name (case-insensitive substring)."""
    pattern = f"%{query.lower()}%"
    rows = conn.execute(
        """
        SELECT provider_team_id, name, country, is_national, code, founded,
               venue_name, venue_city
        FROM team_identity
        WHERE lower(name) LIKE ?
           OR (code IS NOT NULL AND lower(code) LIKE ?)
        ORDER BY name
        LIMIT ?
        """,
        [pattern, pattern, limit],
    ).fetchall()
    cols = [
        "provider_team_id", "name", "country", "is_national",
        "code", "founded", "venue_name", "venue_city",
    ]
    return [dict(zip(cols, r)) for r in rows]


def find_player(conn: duckdb.DuckDBPyConnection, query: str, limit: int = 10) -> list[dict]:
    """Search players by name (case-insensitive substring)."""
    pattern = f"%{query.lower()}%"
    rows = conn.execute(
        """
        SELECT p.provider_player_id, p.name, p.nationality, p.age,
               p.birth_country, p.injured
        FROM player_identity p
        WHERE lower(p.name) LIKE ?
        ORDER BY p.name
        LIMIT ?
        """,
        [pattern, limit],
    ).fetchall()
    cols = ["provider_player_id", "name", "nationality", "age", "birth_country", "injured"]
    return [dict(zip(cols, r)) for r in rows]


def get_team_memberships(
    conn: duckdb.DuckDBPyConnection, provider_team_id: int
) -> list[dict]:
    """Return all competition memberships for a team."""
    rows = conn.execute(
        """
        SELECT provider_league_id, season, competition_type, scope, country
        FROM team_season_membership
        WHERE provider_team_id = ?
        ORDER BY season DESC, provider_league_id
        """,
        [provider_team_id],
    ).fetchall()
    cols = ["provider_league_id", "season", "competition_type", "scope", "country"]
    return [dict(zip(cols, r)) for r in rows]


def entity_counts(conn: duckdb.DuckDBPyConnection) -> dict:
    """Return summary counts for all entity tables."""
    counts: dict[str, Any] = {}
    for table, col in [
        ("team_identity", "provider_team_id"),
        ("player_identity", "provider_player_id"),
        ("team_season_membership", "id"),
        ("squad_membership", "id"),
    ]:
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            counts[table] = row[0] if row else 0
        except Exception:
            counts[table] = 0

    # Breakdown
    try:
        clubs = conn.execute(
            "SELECT COUNT(*) FROM team_identity WHERE is_national = FALSE"
        ).fetchone()
        counts["clubs"] = clubs[0] if clubs else 0
        nationals = conn.execute(
            "SELECT COUNT(*) FROM team_identity WHERE is_national = TRUE"
        ).fetchone()
        counts["national_teams"] = nationals[0] if nationals else 0
    except Exception:
        counts["clubs"] = 0
        counts["national_teams"] = 0

    try:
        leagues_with_teams = conn.execute(
            "SELECT COUNT(DISTINCT provider_league_id) FROM team_season_membership"
        ).fetchone()
        counts["leagues_with_teams"] = leagues_with_teams[0] if leagues_with_teams else 0
    except Exception:
        counts["leagues_with_teams"] = 0

    return counts


# ── Entity sync runs ──────────────────────────────────────────────────────────


def create_entity_sync_run(
    conn: duckdb.DuckDBPyConnection,
    mode: str,
    leagues_requested: int = 0,
) -> int:
    """Insert a new entity_sync_run and return its id."""
    row = conn.execute(
        """
        INSERT INTO entity_sync_runs (mode, leagues_requested, status, started_at)
        VALUES (?, ?, 'running', ?)
        RETURNING id
        """,
        [mode, leagues_requested, _NOW()],
    ).fetchone()
    return row[0] if row else -1


def finish_entity_sync_run(
    conn: duckdb.DuckDBPyConnection,
    run_id: int,
    *,
    status: str = "completed",
    teams_synced: int = 0,
    memberships_synced: int = 0,
    players_synced: int = 0,
    squads_synced: int = 0,
    api_calls: int = 0,
    error_message: str | None = None,
) -> None:
    """Update an entity_sync_run with final stats."""
    conn.execute(
        """
        UPDATE entity_sync_runs
        SET finished_at       = ?,
            status            = ?,
            teams_synced      = ?,
            memberships_synced = ?,
            players_synced    = ?,
            squads_synced     = ?,
            api_calls         = ?,
            error_message     = ?
        WHERE id = ?
        """,
        [
            _NOW(), status,
            teams_synced, memberships_synced, players_synced, squads_synced,
            api_calls, error_message,
            run_id,
        ],
    )
