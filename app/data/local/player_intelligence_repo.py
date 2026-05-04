"""Phase 11: Local DuckDB repository for player intelligence data."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

_NOW = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731


# ── Upserts ───────────────────────────────────────────────────────────────────

def upsert_player_fixture_stats(
    conn: "duckdb.DuckDBPyConnection",
    rows: list[dict],
) -> int:
    """Insert-or-update player stats for a fixture. Returns number of rows written."""
    if not rows:
        return 0
    written = 0
    now = _NOW()
    for r in rows:
        try:
            conn.execute(
                """
                INSERT INTO player_fixture_stats (
                    id, provider_fixture_id, fixture_id, league_id, season,
                    team_id, player_id, player_name, team_name, position,
                    minutes, rating, captain, substitute, offsides,
                    shots_total, shots_on, goals_total, goals_conceded, assists,
                    saves, passes_total, passes_key, passes_accuracy,
                    tackles_total, tackles_blocks, tackles_interceptions,
                    duels_total, duels_won, dribbles_attempts, dribbles_success,
                    fouls_drawn, fouls_committed, cards_yellow, cards_red,
                    penalty_won, penalty_committed, penalty_scored, penalty_missed,
                    penalty_saved, synced_at, raw_json
                ) VALUES (
                    nextval('player_fixture_stats_seq'), ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?
                )
                ON CONFLICT (player_id, provider_fixture_id) DO UPDATE SET
                    minutes = excluded.minutes,
                    rating  = excluded.rating,
                    goals_total = excluded.goals_total,
                    assists = excluded.assists,
                    shots_total = excluded.shots_total,
                    shots_on = excluded.shots_on,
                    cards_yellow = excluded.cards_yellow,
                    cards_red = excluded.cards_red,
                    fouls_drawn = excluded.fouls_drawn,
                    fouls_committed = excluded.fouls_committed,
                    passes_key = excluded.passes_key,
                    synced_at = excluded.synced_at,
                    raw_json = excluded.raw_json
                """,
                [
                    r.get("provider_fixture_id"), r.get("fixture_id"),
                    r.get("league_id"), r.get("season"),
                    r.get("team_id"), r.get("player_id"),
                    r.get("player_name"), r.get("team_name"), r.get("position"),
                    r.get("minutes", 0), r.get("rating"),
                    bool(r.get("captain", False)), bool(r.get("substitute", False)),
                    r.get("offsides", 0),
                    r.get("shots_total", 0), r.get("shots_on", 0),
                    r.get("goals_total", 0), r.get("goals_conceded", 0),
                    r.get("assists", 0), r.get("saves", 0),
                    r.get("passes_total", 0), r.get("passes_key", 0),
                    r.get("passes_accuracy"),
                    r.get("tackles_total", 0), r.get("tackles_blocks", 0),
                    r.get("tackles_interceptions", 0),
                    r.get("duels_total", 0), r.get("duels_won", 0),
                    r.get("dribbles_attempts", 0), r.get("dribbles_success", 0),
                    r.get("fouls_drawn", 0), r.get("fouls_committed", 0),
                    r.get("cards_yellow", 0), r.get("cards_red", 0),
                    r.get("penalty_won", 0), r.get("penalty_committed", 0),
                    r.get("penalty_scored", 0), r.get("penalty_missed", 0),
                    r.get("penalty_saved", 0),
                    now, r.get("raw_json"),
                ],
            )
            written += 1
        except Exception as exc:
            logger.debug("upsert_player_fixture_stats: player_id=%s — %s", r.get("player_id"), exc)
    return written


def upsert_player_season_profile(
    conn: "duckdb.DuckDBPyConnection",
    profile: dict,
) -> None:
    """Insert-or-update a player season profile."""
    now = _NOW()
    conn.execute(
        """
        INSERT INTO player_season_profiles (
            id, player_id, player_name, team_id, team_name,
            league_id, season, position, appearances, starts,
            total_minutes, avg_minutes, goals, assists,
            shots_total, shots_on, shots_on_rate, key_passes,
            fouls_drawn, fouls_committed, yellow_cards, red_cards,
            avg_rating, last_updated, raw_json
        ) VALUES (
            nextval('player_season_profiles_seq'), ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?
        )
        ON CONFLICT (player_id, team_id, league_id, season) DO UPDATE SET
            player_name  = excluded.player_name,
            team_name    = excluded.team_name,
            position     = excluded.position,
            appearances  = excluded.appearances,
            starts       = excluded.starts,
            total_minutes = excluded.total_minutes,
            avg_minutes  = excluded.avg_minutes,
            goals        = excluded.goals,
            assists      = excluded.assists,
            shots_total  = excluded.shots_total,
            shots_on     = excluded.shots_on,
            shots_on_rate = excluded.shots_on_rate,
            key_passes   = excluded.key_passes,
            fouls_drawn  = excluded.fouls_drawn,
            fouls_committed = excluded.fouls_committed,
            yellow_cards = excluded.yellow_cards,
            red_cards    = excluded.red_cards,
            avg_rating   = excluded.avg_rating,
            last_updated = excluded.last_updated,
            raw_json     = excluded.raw_json
        """,
        [
            profile.get("player_id"), profile.get("player_name"),
            profile.get("team_id"), profile.get("team_name"),
            profile.get("league_id"), profile.get("season"),
            profile.get("position"), profile.get("appearances", 0),
            profile.get("starts", 0), profile.get("total_minutes", 0),
            profile.get("avg_minutes"), profile.get("goals", 0),
            profile.get("assists", 0), profile.get("shots_total", 0),
            profile.get("shots_on", 0), profile.get("shots_on_rate"),
            profile.get("key_passes", 0), profile.get("fouls_drawn", 0),
            profile.get("fouls_committed", 0), profile.get("yellow_cards", 0),
            profile.get("red_cards", 0), profile.get("avg_rating"),
            now, profile.get("raw_json"),
        ],
    )


def upsert_player_recent_form(
    conn: "duckdb.DuckDBPyConnection",
    form: dict,
) -> None:
    """Insert-or-update player recent form for a given window."""
    now = _NOW()
    conn.execute(
        """
        INSERT INTO player_recent_form (
            id, player_id, team_id, league_id, season, window_size,
            matches_count, avg_minutes, goals, assists, shots_total, shots_on,
            key_passes, fouls_drawn, fouls_committed, cards_total,
            avg_rating, trend_label, updated_at
        ) VALUES (
            nextval('player_recent_form_seq'), ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?
        )
        ON CONFLICT (player_id, team_id, league_id, season, window_size) DO UPDATE SET
            matches_count   = excluded.matches_count,
            avg_minutes     = excluded.avg_minutes,
            goals           = excluded.goals,
            assists         = excluded.assists,
            shots_total     = excluded.shots_total,
            shots_on        = excluded.shots_on,
            key_passes      = excluded.key_passes,
            fouls_drawn     = excluded.fouls_drawn,
            fouls_committed = excluded.fouls_committed,
            cards_total     = excluded.cards_total,
            avg_rating      = excluded.avg_rating,
            trend_label     = excluded.trend_label,
            updated_at      = excluded.updated_at
        """,
        [
            form.get("player_id"), form.get("team_id"),
            form.get("league_id"), form.get("season"),
            form.get("window_size", 5), form.get("matches_count", 0),
            form.get("avg_minutes"), form.get("goals", 0),
            form.get("assists", 0), form.get("shots_total", 0),
            form.get("shots_on", 0), form.get("key_passes", 0),
            form.get("fouls_drawn", 0), form.get("fouls_committed", 0),
            form.get("cards_total", 0), form.get("avg_rating"),
            form.get("trend_label", "insufficient_data"), now,
        ],
    )


def upsert_player_prop_signal(
    conn: "duckdb.DuckDBPyConnection",
    signal: dict,
) -> None:
    """Insert-or-update a player prop signal."""
    now = _NOW()
    conn.execute(
        """
        INSERT INTO player_prop_signals (
            id, created_at, fixture_id, provider_fixture_id,
            league_id, season, team_id, player_id, player_name,
            market_key, signal_type, confidence_score, trend_score,
            availability_score, minutes_projection, reason_text,
            risk_text, status, metadata_json
        ) VALUES (
            nextval('player_prop_signals_seq'), ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?
        )
        ON CONFLICT (player_id, provider_fixture_id, market_key) DO UPDATE SET
            confidence_score   = excluded.confidence_score,
            trend_score        = excluded.trend_score,
            availability_score = excluded.availability_score,
            minutes_projection = excluded.minutes_projection,
            reason_text        = excluded.reason_text,
            risk_text          = excluded.risk_text,
            status             = excluded.status,
            created_at         = excluded.created_at,
            metadata_json      = excluded.metadata_json
        """,
        [
            now, signal.get("fixture_id"), signal.get("provider_fixture_id"),
            signal.get("league_id"), signal.get("season"),
            signal.get("team_id"), signal.get("player_id"),
            signal.get("player_name"), signal.get("market_key"),
            signal.get("signal_type", "prop_signal"),
            signal.get("confidence_score"), signal.get("trend_score"),
            signal.get("availability_score"), signal.get("minutes_projection"),
            signal.get("reason_text"), signal.get("risk_text"),
            signal.get("status", "observed"),
            json.dumps(signal.get("metadata")) if signal.get("metadata") else None,
        ],
    )


# ── Reads ─────────────────────────────────────────────────────────────────────

def get_player_profile(
    conn: "duckdb.DuckDBPyConnection",
    player_id: int,
) -> dict | None:
    """Return the most recent season profile for a player."""
    row = conn.execute(
        """
        SELECT player_id, player_name, team_id, team_name, league_id, season,
               position, appearances, starts, total_minutes, avg_minutes,
               goals, assists, shots_total, shots_on, shots_on_rate,
               key_passes, fouls_drawn, fouls_committed,
               yellow_cards, red_cards, avg_rating, last_updated
        FROM player_season_profiles
        WHERE player_id = ?
        ORDER BY season DESC, last_updated DESC
        LIMIT 1
        """,
        [player_id],
    ).fetchone()
    if not row:
        return None
    cols = [
        "player_id", "player_name", "team_id", "team_name", "league_id", "season",
        "position", "appearances", "starts", "total_minutes", "avg_minutes",
        "goals", "assists", "shots_total", "shots_on", "shots_on_rate",
        "key_passes", "fouls_drawn", "fouls_committed",
        "yellow_cards", "red_cards", "avg_rating", "last_updated",
    ]
    return dict(zip(cols, row))


def search_players(
    conn: "duckdb.DuckDBPyConnection",
    query: str,
    limit: int = 5,
) -> list[dict]:
    """Search players by name in player_season_profiles."""
    rows = conn.execute(
        """
        SELECT DISTINCT player_id, player_name, team_name, league_id, season, position
        FROM player_season_profiles
        WHERE LOWER(player_name) LIKE ?
        ORDER BY season DESC, appearances DESC
        LIMIT ?
        """,
        [f"%{query.lower()}%", limit],
    ).fetchall()
    cols = ["player_id", "player_name", "team_name", "league_id", "season", "position"]
    return [dict(zip(cols, r)) for r in rows]


def get_player_recent_form(
    conn: "duckdb.DuckDBPyConnection",
    player_id: int,
    window: int = 5,
) -> dict | None:
    """Return recent form for a player at a given window size."""
    row = conn.execute(
        """
        SELECT player_id, team_id, league_id, season, window_size,
               matches_count, avg_minutes, goals, assists, shots_total,
               shots_on, key_passes, fouls_drawn, fouls_committed,
               cards_total, avg_rating, trend_label, updated_at
        FROM player_recent_form
        WHERE player_id = ? AND window_size = ?
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        [player_id, window],
    ).fetchone()
    if not row:
        return None
    cols = [
        "player_id", "team_id", "league_id", "season", "window_size",
        "matches_count", "avg_minutes", "goals", "assists", "shots_total",
        "shots_on", "key_passes", "fouls_drawn", "fouls_committed",
        "cards_total", "avg_rating", "trend_label", "updated_at",
    ]
    return dict(zip(cols, row))


def get_team_player_profiles(
    conn: "duckdb.DuckDBPyConnection",
    team_id: int,
    season: int | None = None,
    limit: int = 20,
) -> list[dict]:
    """Return profiles for all players in a team, optionally filtered by season."""
    if season:
        rows = conn.execute(
            """
            SELECT player_id, player_name, position, appearances, goals,
                   assists, shots_total, yellow_cards, red_cards, avg_rating, season
            FROM player_season_profiles
            WHERE team_id = ? AND season = ?
            ORDER BY appearances DESC, goals DESC
            LIMIT ?
            """,
            [team_id, season, limit],
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT player_id, player_name, position, appearances, goals,
                   assists, shots_total, yellow_cards, red_cards, avg_rating, season
            FROM player_season_profiles
            WHERE team_id = ?
            ORDER BY season DESC, appearances DESC, goals DESC
            LIMIT ?
            """,
            [team_id, limit],
        ).fetchall()
    cols = [
        "player_id", "player_name", "position", "appearances", "goals",
        "assists", "shots_total", "yellow_cards", "red_cards", "avg_rating", "season",
    ]
    return [dict(zip(cols, r)) for r in rows]


def get_fixture_player_signals(
    conn: "duckdb.DuckDBPyConnection",
    provider_fixture_id: int,
) -> list[dict]:
    """Return all prop signals for a fixture, ordered by confidence."""
    rows = conn.execute(
        """
        SELECT player_id, player_name, team_id, market_key, signal_type,
               confidence_score, trend_score, availability_score,
               minutes_projection, reason_text, risk_text, status
        FROM player_prop_signals
        WHERE provider_fixture_id = ?
        ORDER BY confidence_score DESC NULLS LAST
        """,
        [provider_fixture_id],
    ).fetchall()
    cols = [
        "player_id", "player_name", "team_id", "market_key", "signal_type",
        "confidence_score", "trend_score", "availability_score",
        "minutes_projection", "reason_text", "risk_text", "status",
    ]
    return [dict(zip(cols, r)) for r in rows]


def get_top_players_by_market(
    conn: "duckdb.DuckDBPyConnection",
    market_key: str,
    league_id: int | None = None,
    limit: int = 10,
) -> list[dict]:
    """Return top players by confidence for a specific market signal."""
    if league_id:
        rows = conn.execute(
            """
            SELECT player_id, player_name, team_id, market_key,
                   confidence_score, trend_score, reason_text, status,
                   created_at
            FROM player_prop_signals
            WHERE market_key = ? AND league_id = ?
            ORDER BY confidence_score DESC NULLS LAST, created_at DESC
            LIMIT ?
            """,
            [market_key, league_id, limit],
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT player_id, player_name, team_id, market_key,
                   confidence_score, trend_score, reason_text, status,
                   created_at
            FROM player_prop_signals
            WHERE market_key = ?
            ORDER BY confidence_score DESC NULLS LAST, created_at DESC
            LIMIT ?
            """,
            [market_key, limit],
        ).fetchall()
    cols = [
        "player_id", "player_name", "team_id", "market_key",
        "confidence_score", "trend_score", "reason_text", "status", "created_at",
    ]
    return [dict(zip(cols, r)) for r in rows]


def get_player_fixture_history(
    conn: "duckdb.DuckDBPyConnection",
    player_id: int,
    limit: int = 10,
) -> list[dict]:
    """Return fixture-level stats history for a player, most recent first."""
    rows = conn.execute(
        """
        SELECT provider_fixture_id, team_name, minutes, rating,
               goals_total, assists, shots_total, shots_on,
               cards_yellow, cards_red, fouls_committed, synced_at
        FROM player_fixture_stats
        WHERE player_id = ? AND minutes > 0
        ORDER BY synced_at DESC
        LIMIT ?
        """,
        [player_id, limit],
    ).fetchall()
    cols = [
        "provider_fixture_id", "team_name", "minutes", "rating",
        "goals_total", "assists", "shots_total", "shots_on",
        "cards_yellow", "cards_red", "fouls_committed", "synced_at",
    ]
    return [dict(zip(cols, r)) for r in rows]


def get_hot_players(
    conn: "duckdb.DuckDBPyConnection",
    league_id: int | None = None,
    market_key: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Return players with 'hot' trend label or high confidence signals."""
    try:
        if league_id and market_key:
            rows = conn.execute(
                """
                SELECT pf.player_id, pf.player_name, pf.team_name,
                       pf.trend_label, pf.goals, pf.assists,
                       pf.shots_total, pf.avg_rating, pf.matches_count, pf.avg_minutes
                FROM player_recent_form pf
                JOIN player_season_profiles psp
                    ON psp.player_id = pf.player_id AND psp.league_id = pf.league_id
                WHERE pf.trend_label IN ('hot', 'stable')
                  AND pf.league_id = ?
                ORDER BY pf.goals DESC NULLS LAST, pf.avg_rating DESC NULLS LAST
                LIMIT ?
                """,
                [league_id, limit],
            ).fetchall()
        elif league_id:
            rows = conn.execute(
                """
                SELECT player_id, player_name, team_id AS team_name,
                       trend_label, goals, assists,
                       shots_total, avg_rating, matches_count, avg_minutes
                FROM player_recent_form
                WHERE trend_label IN ('hot', 'stable') AND league_id = ?
                ORDER BY goals DESC NULLS LAST, avg_rating DESC NULLS LAST
                LIMIT ?
                """,
                [league_id, limit],
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT player_id, player_name, team_id AS team_name,
                       trend_label, goals, assists,
                       shots_total, avg_rating, matches_count, avg_minutes
                FROM player_recent_form
                WHERE trend_label IN ('hot', 'stable')
                ORDER BY goals DESC NULLS LAST, avg_rating DESC NULLS LAST
                LIMIT ?
                """,
                [limit],
            ).fetchall()
    except Exception as exc:
        logger.debug("get_hot_players: %s", exc)
        return []

    cols = [
        "player_id", "player_name", "team_name", "trend_label",
        "goals", "assists", "shots_total", "avg_rating", "matches_count", "avg_minutes",
    ]
    return [dict(zip(cols, r)) for r in rows]


def audit_player_intelligence(conn: "duckdb.DuckDBPyConnection") -> dict:
    """Return aggregate counts for the audit script."""
    result: dict[str, Any] = {}
    for table in ("player_fixture_stats", "player_season_profiles", "player_recent_form", "player_prop_signals"):
        try:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            result[table] = cnt
        except Exception:
            result[table] = "error"
    try:
        result["distinct_players"] = conn.execute(
            "SELECT COUNT(DISTINCT player_id) FROM player_fixture_stats"
        ).fetchone()[0]
        result["distinct_teams"] = conn.execute(
            "SELECT COUNT(DISTINCT team_id) FROM player_fixture_stats"
        ).fetchone()[0]
        result["distinct_leagues"] = conn.execute(
            "SELECT COUNT(DISTINCT league_id) FROM player_fixture_stats"
        ).fetchone()[0]
        result["distinct_fixtures"] = conn.execute(
            "SELECT COUNT(DISTINCT provider_fixture_id) FROM player_fixture_stats"
        ).fetchone()[0]
    except Exception as exc:
        result["counts_error"] = str(exc)
    return result
