"""Read/write operations for the local DuckDB history database.

These functions are the insertion and query interface for Phase 2.
Phase 3 (backfill from API-Football / Supabase export) will call these to
populate the historical tables.

No connection to Supabase or the daily sync pipeline — pure local DuckDB.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import duckdb

logger = logging.getLogger(__name__)


# ── Fixtures ──────────────────────────────────────────────────────────────────


def insert_fixture(conn: duckdb.DuckDBPyConnection, row: dict) -> None:
    """Insert or replace a completed fixture into fixtures_history.

    Required keys: id, provider_fixture_id, provider_league_id, season,
                   home_team_id, away_team_id, kickoff_at.
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO fixtures_history (
            id, provider_fixture_id, provider_league_id, league_name,
            season, home_team_id, home_team_name, away_team_id, away_team_name,
            kickoff_at, date_local, status_short,
            goals_home, goals_away, halftime_home, halftime_away, venue_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            row["id"],
            row["provider_fixture_id"],
            row["provider_league_id"],
            row.get("league_name"),
            row["season"],
            row["home_team_id"],
            row.get("home_team_name"),
            row["away_team_id"],
            row.get("away_team_name"),
            row["kickoff_at"],
            row.get("date_local"),
            row.get("status_short"),
            row.get("goals_home"),
            row.get("goals_away"),
            row.get("halftime_home"),
            row.get("halftime_away"),
            row.get("venue_name"),
        ],
    )


def count_fixtures(conn: duckdb.DuckDBPyConnection) -> int:
    return conn.execute("SELECT COUNT(*) FROM fixtures_history").fetchone()[0]


# ── Published picks ───────────────────────────────────────────────────────────


def insert_published_pick(conn: duckdb.DuckDBPyConnection, row: dict) -> None:
    """Insert or replace a published pick into published_picks_history.

    Required keys: id, provider_fixture_id, market_key, selection.
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO published_picks_history (
            id, fixture_history_id, provider_fixture_id,
            market_key, selection,
            model_probability, implied_probability, edge, confidence_score,
            best_odd, best_bookmaker, published_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            row["id"],
            row.get("fixture_history_id"),
            row["provider_fixture_id"],
            row["market_key"],
            row["selection"],
            row.get("model_probability"),
            row.get("implied_probability"),
            row.get("edge"),
            row.get("confidence_score"),
            row.get("best_odd"),
            row.get("best_bookmaker"),
            row.get("published_at"),
        ],
    )


# ── Standings ─────────────────────────────────────────────────────────────────


def insert_standing(conn: duckdb.DuckDBPyConnection, row: dict) -> None:
    """Insert a standings row into standings_history (INSERT OR IGNORE).

    Required keys: provider_league_id, season, team_id, snapshot_date.
    UNIQUE constraint: (provider_league_id, season, team_id, snapshot_date).
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO standings_history (
            provider_league_id, league_name, season,
            team_id, team_name,
            rank, points, played, won, drawn, lost,
            goals_for, goals_against, goal_diff, form,
            snapshot_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            row["provider_league_id"],
            row.get("league_name"),
            row["season"],
            row["team_id"],
            row.get("team_name"),
            row.get("rank"),
            row.get("points"),
            row.get("played"),
            row.get("won"),
            row.get("drawn"),
            row.get("lost"),
            row.get("goals_for"),
            row.get("goals_against"),
            row.get("goal_diff"),
            row.get("form"),
            row["snapshot_date"],
        ],
    )


# ── Team stats ────────────────────────────────────────────────────────────────


def insert_team_stat(conn: duckdb.DuckDBPyConnection, row: dict) -> None:
    """Insert a team statistics row into team_stats_history (INSERT OR IGNORE).

    Required keys: provider_league_id, season, team_id, snapshot_date.
    UNIQUE constraint: (provider_league_id, season, team_id, snapshot_date).
    raw_stats is serialised to JSON string before storage.
    """
    import json as _json

    raw = row.get("raw_stats")
    conn.execute(
        """
        INSERT OR IGNORE INTO team_stats_history (
            provider_league_id, season, team_id, team_name,
            games_played, wins, draws, losses,
            goals_for, goals_against, clean_sheets,
            avg_goals_scored, avg_goals_conceded, form_last5,
            raw_stats, snapshot_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            row["provider_league_id"],
            row["season"],
            row["team_id"],
            row.get("team_name"),
            row.get("games_played"),
            row.get("wins"),
            row.get("draws"),
            row.get("losses"),
            row.get("goals_for"),
            row.get("goals_against"),
            row.get("clean_sheets"),
            row.get("avg_goals_scored"),
            row.get("avg_goals_conceded"),
            row.get("form_last5"),
            _json.dumps(raw) if raw else None,
            row["snapshot_date"],
        ],
    )


def count_rows_for_season(
    conn: duckdb.DuckDBPyConnection, table: str, provider_league_id: int, season: int
) -> int:
    """Return row count for a league/season in the given history table."""
    allowed = {
        "fixtures_history", "standings_history", "team_stats_history",
        "published_picks_history", "pick_results_history",
    }
    if table not in allowed:
        raise ValueError(f"Tabla no permitida: {table!r}")
    return conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE provider_league_id = ? AND season = ?",
        [provider_league_id, season],
    ).fetchone()[0]


# ── Pick results ──────────────────────────────────────────────────────────────


def insert_pick_result(conn: duckdb.DuckDBPyConnection, row: dict) -> None:
    """Insert or replace a settled pick result into pick_results_history.

    Required keys: id, market_key, selection, odd_taken, result_status.
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO pick_results_history (
            id, published_pick_id, fixture_history_id,
            market_key, selection, odd_taken,
            result_status, settled_at, profit_units
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            row["id"],
            row.get("published_pick_id"),
            row.get("fixture_history_id"),
            row["market_key"],
            row["selection"],
            row["odd_taken"],
            row["result_status"],
            row.get("settled_at"),
            row.get("profit_units"),
        ],
    )


# ── Backtest ──────────────────────────────────────────────────────────────────


def create_backtest_run(
    conn: duckdb.DuckDBPyConnection,
    run_name: str,
    *,
    description: str | None = None,
    leagues: list[int] | None = None,
    seasons: list[int] | None = None,
    min_edge: float | None = None,
    min_confidence: float | None = None,
    max_daily_picks: int | None = None,
    config: dict | None = None,
) -> int:
    """Create a new backtest_run record. Returns the new run id."""
    conn.execute(
        """
        INSERT INTO backtest_runs (
            run_name, description, leagues, seasons,
            min_edge, min_confidence, max_daily_picks, config_json, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running')
        """,
        [
            run_name,
            description,
            json.dumps(leagues) if leagues is not None else None,
            json.dumps(seasons) if seasons is not None else None,
            min_edge,
            min_confidence,
            max_daily_picks,
            json.dumps(config) if config is not None else None,
        ],
    )
    run_id: int = conn.execute("SELECT currval('seq_backtest_runs')").fetchone()[0]
    logger.info("Backtest run creado: id=%d name=%s", run_id, run_name)
    return run_id


def get_completed_backtest_run(
    conn: duckdb.DuckDBPyConnection, run_name: str
) -> int | None:
    """Return the id of an existing completed backtest run with this name, or None."""
    row = conn.execute(
        "SELECT id FROM backtest_runs WHERE run_name = ? AND status = 'completed' LIMIT 1",
        [run_name],
    ).fetchone()
    return row[0] if row else None


def finish_backtest_run(
    conn: duckdb.DuckDBPyConnection,
    run_id: int,
    *,
    status: str = "completed",
    total_picks: int = 0,
    total_profit_units: float = 0.0,
    roi_pct: float = 0.0,
) -> None:
    conn.execute(
        """
        UPDATE backtest_runs
        SET finished_at = current_timestamp,
            status = ?,
            total_picks = ?,
            total_profit_units = ?,
            roi_pct = ?
        WHERE id = ?
        """,
        [status, total_picks, total_profit_units, roi_pct, run_id],
    )


def insert_backtest_metric(conn: duckdb.DuckDBPyConnection, run_id: int, row: dict) -> None:
    """Insert one per-pick backtest result row.

    Required keys in row: market_key, selection.
    Optional: is_selected (bool, default True) — False for cap-rejected candidates.
    Requires schema 002 (ALTER TABLE adds is_selected column).
    """
    conn.execute(
        """
        INSERT INTO backtest_metrics (
            backtest_run_id, fixture_history_id, market_key, selection,
            model_probability, implied_probability, edge, confidence_score,
            odd_taken, result_status, profit_units, kickoff_at, is_selected
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            run_id,
            row.get("fixture_history_id"),
            row["market_key"],
            row["selection"],
            row.get("model_probability"),
            row.get("implied_probability"),
            row.get("edge"),
            row.get("confidence_score"),
            row.get("odd_taken"),
            row.get("result_status"),
            row.get("profit_units"),
            row.get("kickoff_at"),
            row.get("is_selected", True),
        ],
    )


# ── Analytics queries ─────────────────────────────────────────────────────────


def get_roi_summary(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Overall ROI across all settled picks in pick_results_history."""
    row = conn.execute(
        """
        SELECT
            COUNT(*)                                         AS total,
            COALESCE(SUM(profit_units), 0)                  AS profit,
            COUNT(*) FILTER (WHERE result_status = 'win')   AS wins,
            COUNT(*) FILTER (WHERE result_status = 'loss')  AS losses,
            COUNT(*) FILTER (WHERE result_status = 'void')  AS voids
        FROM pick_results_history
        WHERE result_status IN ('win', 'loss', 'void')
        """
    ).fetchone()
    total, profit, wins, losses, voids = row
    settled = wins + losses + voids
    roi_pct = round((profit / settled) * 100, 2) if settled else 0.0
    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "voids": voids,
        "profit_units": round(profit, 4),
        "roi_pct": roi_pct,
    }


def get_roi_by_market(conn: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """ROI breakdown by market_key."""
    rows = conn.execute(
        """
        SELECT
            market_key,
            COUNT(*)                                        AS picks,
            COUNT(*) FILTER (WHERE result_status = 'win')  AS wins,
            COALESCE(SUM(profit_units), 0)                  AS profit
        FROM pick_results_history
        WHERE result_status IN ('win', 'loss', 'void')
        GROUP BY market_key
        ORDER BY profit DESC
        """
    ).fetchall()
    return [
        {"market": r[0], "picks": r[1], "wins": r[2], "profit_units": round(r[3], 4)}
        for r in rows
    ]


def get_roi_by_league(conn: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """ROI breakdown by league (joins via fixtures_history)."""
    rows = conn.execute(
        """
        SELECT
            f.provider_league_id,
            f.league_name,
            COUNT(*)                                        AS picks,
            COUNT(*) FILTER (WHERE r.result_status='win')  AS wins,
            COALESCE(SUM(r.profit_units), 0)               AS profit
        FROM pick_results_history r
        LEFT JOIN fixtures_history f ON f.id = r.fixture_history_id
        WHERE r.result_status IN ('win', 'loss', 'void')
        GROUP BY f.provider_league_id, f.league_name
        ORDER BY profit DESC
        """
    ).fetchall()
    return [
        {
            "league_id": r[0],
            "league_name": r[1],
            "picks": r[2],
            "wins": r[3],
            "profit_units": round(r[4], 4),
        }
        for r in rows
    ]


def get_backtest_run_summary(conn: duckdb.DuckDBPyConnection, run_id: int) -> dict[str, Any] | None:
    """Return the summary for a specific backtest run."""
    row = conn.execute(
        """
        SELECT id, run_name, status, total_picks, total_profit_units, roi_pct,
               CAST(started_at  AS VARCHAR) AS started_at,
               CAST(finished_at AS VARCHAR) AS finished_at
        FROM backtest_runs WHERE id = ?
        """,
        [run_id],
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "run_name": row[1],
        "status": row[2],
        "total_picks": row[3],
        "total_profit_units": row[4],
        "roi_pct": row[5],
        "started_at": row[6],
        "finished_at": row[7],
    }


# ── History metadata (internal ingest state) ──────────────────────────────────


def get_metadata(
    conn: duckdb.DuckDBPyConnection, key: str, default: str | None = None
) -> str | None:
    """Return the stored value for key, or default if not found."""
    row = conn.execute(
        "SELECT value FROM history_metadata WHERE key = ?", [key]
    ).fetchone()
    return row[0] if row else default


def set_metadata(conn: duckdb.DuckDBPyConnection, key: str, value: str) -> None:
    """Upsert a key-value pair in history_metadata."""
    conn.execute(
        "INSERT OR REPLACE INTO history_metadata (key, value, updated_at) "
        "VALUES (?, ?, current_timestamp)",
        [key, value],
    )


# ── Season-level queries for rolling-window policy ────────────────────────────


def get_seasons_in_history(
    conn: duckdb.DuckDBPyConnection, provider_league_id: int
) -> list[int]:
    """Return sorted list of seasons present in fixtures_history for a league."""
    rows = conn.execute(
        "SELECT DISTINCT season FROM fixtures_history "
        "WHERE provider_league_id = ? ORDER BY season ASC",
        [provider_league_id],
    ).fetchall()
    return [r[0] for r in rows]


def delete_season_from_history(
    conn: duckdb.DuckDBPyConnection, provider_league_id: int, season: int
) -> int:
    """Delete all historical rows for a league/season (fixtures + picks).

    Cascade order: pick_results_history → published_picks_history → fixtures_history.
    Returns the number of fixture rows deleted.
    """
    conn.execute(
        """
        DELETE FROM pick_results_history
        WHERE fixture_history_id IN (
            SELECT id FROM fixtures_history
            WHERE provider_league_id = ? AND season = ?
        )
        """,
        [provider_league_id, season],
    )
    conn.execute(
        """
        DELETE FROM published_picks_history
        WHERE fixture_history_id IN (
            SELECT id FROM fixtures_history
            WHERE provider_league_id = ? AND season = ?
        )
        """,
        [provider_league_id, season],
    )
    count = conn.execute(
        "SELECT COUNT(*) FROM fixtures_history WHERE provider_league_id = ? AND season = ?",
        [provider_league_id, season],
    ).fetchone()[0]
    conn.execute(
        "DELETE FROM fixtures_history WHERE provider_league_id = ? AND season = ?",
        [provider_league_id, season],
    )
    return count


# ── Phase 5: competition context ──────────────────────────────────────────────


def get_leagues_in_history(conn: duckdb.DuckDBPyConnection) -> list[int]:
    """Return all distinct provider_league_id values present in fixtures_history."""
    rows = conn.execute(
        "SELECT DISTINCT provider_league_id FROM fixtures_history ORDER BY provider_league_id"
    ).fetchall()
    return [r[0] for r in rows]


def get_competition_context(
    conn: duckdb.DuckDBPyConnection, league_id: int
) -> dict | None:
    """Return competition_context row for league_id, or None if not seeded."""
    row = conn.execute(
        """
        SELECT provider_league_id, league_name, country,
               entity_scope, competition_type, elo_weight
        FROM competition_context WHERE provider_league_id = ?
        """,
        [league_id],
    ).fetchone()
    if not row:
        return None
    return {
        "provider_league_id": row[0],
        "league_name": row[1],
        "country": row[2],
        "entity_scope": row[3],
        "competition_type": row[4],
        "elo_weight": row[5],
    }


def insert_competition_context(conn: duckdb.DuckDBPyConnection, row: dict) -> None:
    """INSERT OR IGNORE a competition_context row.

    Required keys: provider_league_id, entity_scope, competition_type.
    Optional: league_name, country, elo_weight (defaults to 1.0).
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO competition_context
            (provider_league_id, league_name, country,
             entity_scope, competition_type, elo_weight)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            row["provider_league_id"],
            row.get("league_name"),
            row.get("country"),
            row["entity_scope"],
            row["competition_type"],
            row.get("elo_weight", 1.0),
        ],
    )


# ── Phase 5: fixtures for backtest ────────────────────────────────────────────


def get_fixtures_for_season(
    conn: duckdb.DuckDBPyConnection,
    provider_league_id: int,
    season: int,
    *,
    only_completed: bool = True,
) -> list[dict]:
    """Return fixtures for a league/season ordered by kickoff_at.

    only_completed=True (default): only rows where goals_home and goals_away
    are both non-NULL (i.e., match has a final score).
    kickoff_at is cast to VARCHAR to avoid pytz dependency on Windows.
    """
    where_goals = "AND goals_home IS NOT NULL AND goals_away IS NOT NULL" if only_completed else ""
    rows = conn.execute(
        f"""
        SELECT id, home_team_id, away_team_id,
               goals_home, goals_away,
               CAST(kickoff_at AS VARCHAR) AS kickoff_at,
               status_short
        FROM fixtures_history
        WHERE provider_league_id = ? AND season = ?
          {where_goals}
        ORDER BY kickoff_at
        """,
        [provider_league_id, season],
    ).fetchall()
    return [
        {
            "id": r[0],
            "home_team_id": r[1],
            "away_team_id": r[2],
            "goals_home": r[3],
            "goals_away": r[4],
            "kickoff_at": r[5],
            "status_short": r[6],
        }
        for r in rows
    ]


def _empty_team_stats() -> dict:
    """Return a zero-filled team stats dict with all expected keys."""
    return {
        "home_scored":   0.0,
        "home_conceded": 0.0,
        "home_games":    0,
        "away_scored":   0.0,
        "away_conceded": 0.0,
        "away_games":    0,
    }


def get_team_goals_stats(
    conn: duckdb.DuckDBPyConnection,
    provider_league_id: int,
    seasons: list[int],
) -> dict[int, dict]:
    """Return per-team goal averages aggregated from fixtures_history.

    Covers all seasons in the list (training window). Only includes fixtures
    with non-NULL goals. Returns a dict keyed by team_id. Every entry
    always contains all six keys (home/away × scored/conceded/games) even if
    a team only appeared as home or only as away in the training window —
    in that case the missing side defaults to 0.0/0.

        {
            'home_scored':   float,  # avg goals scored as home team
            'home_conceded': float,  # avg goals conceded as home team
            'home_games':    int,
            'away_scored':   float,  # avg goals scored as away team
            'away_conceded': float,  # avg goals conceded as away team
            'away_games':    int,
        }
    """
    if not seasons:
        return {}
    placeholders = ", ".join("?" * len(seasons))

    home_rows = conn.execute(
        f"""
        SELECT home_team_id,
               AVG(goals_home) AS home_scored,
               AVG(goals_away) AS home_conceded,
               COUNT(*)        AS home_games
        FROM fixtures_history
        WHERE provider_league_id = ?
          AND season IN ({placeholders})
          AND goals_home IS NOT NULL AND goals_away IS NOT NULL
        GROUP BY home_team_id
        """,
        [provider_league_id, *seasons],
    ).fetchall()

    away_rows = conn.execute(
        f"""
        SELECT away_team_id,
               AVG(goals_away) AS away_scored,
               AVG(goals_home) AS away_conceded,
               COUNT(*)        AS away_games
        FROM fixtures_history
        WHERE provider_league_id = ?
          AND season IN ({placeholders})
          AND goals_home IS NOT NULL AND goals_away IS NOT NULL
        GROUP BY away_team_id
        """,
        [provider_league_id, *seasons],
    ).fetchall()

    result: dict[int, dict] = {}
    for team_id, home_scored, home_conceded, home_games in home_rows:
        entry = result.setdefault(team_id, _empty_team_stats())
        entry["home_scored"]   = home_scored   or 0.0
        entry["home_conceded"] = home_conceded or 0.0
        entry["home_games"]    = home_games
    for team_id, away_scored, away_conceded, away_games in away_rows:
        entry = result.setdefault(team_id, _empty_team_stats())
        entry["away_scored"]   = away_scored   or 0.0
        entry["away_conceded"] = away_conceded or 0.0
        entry["away_games"]    = away_games
    return result


def get_league_goal_averages(
    conn: duckdb.DuckDBPyConnection,
    provider_league_id: int,
    seasons: list[int],
) -> dict[str, float]:
    """Return league-wide avg_home and avg_away goals for the given seasons.

    Used as the neutral baseline in Poisson lambda computation.
    Falls back to conservative defaults (1.3 / 1.1) if no data.
    """
    if not seasons:
        return {"avg_home": 1.3, "avg_away": 1.1}
    placeholders = ", ".join("?" * len(seasons))
    row = conn.execute(
        f"""
        SELECT AVG(goals_home), AVG(goals_away)
        FROM fixtures_history
        WHERE provider_league_id = ?
          AND season IN ({placeholders})
          AND goals_home IS NOT NULL AND goals_away IS NOT NULL
        """,
        [provider_league_id, *seasons],
    ).fetchone()
    return {
        "avg_home": row[0] if row and row[0] is not None else 1.3,
        "avg_away": row[1] if row and row[1] is not None else 1.1,
    }


# ── Phase 5: Elo history ──────────────────────────────────────────────────────


def insert_team_elo(conn: duckdb.DuckDBPyConnection, row: dict) -> None:
    """INSERT OR IGNORE one Elo update row into team_elo_history.

    Required keys: team_id, entity_scope, elo_before, elo_after.
    The UNIQUE index on (team_id, entity_scope, fixture_id) makes
    re-running a backtest idempotent — duplicate rows are silently ignored.

    Note: Elo parameters (K, home_advantage) are baked into elo_after.
    If you change those parameters, delete the existing rows for the affected
    seasons and re-run to regenerate with the new values.
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO team_elo_history (
            team_id, entity_scope, provider_league_id, season,
            fixture_id, kickoff_at, elo_before, elo_after,
            opponent_id, was_home, goals_for, goals_against
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            row["team_id"],
            row["entity_scope"],
            row.get("provider_league_id"),
            row.get("season"),
            row.get("fixture_id"),
            row.get("kickoff_at"),
            row["elo_before"],
            row["elo_after"],
            row.get("opponent_id"),
            row.get("was_home"),
            row.get("goals_for"),
            row.get("goals_against"),
        ],
    )


# ── Phase 5: training samples ─────────────────────────────────────────────────


def insert_training_sample(conn: duckdb.DuckDBPyConnection, row: dict) -> None:
    """Insert one training_samples row for a backtest run.

    Required keys: backtest_run_id, fixture_history_id, market_key, selection.
    Odds-dependent fields (implied_probability, edge, ev_value) should be None
    in Phase 5 (no historical odds).
    """
    conn.execute(
        """
        INSERT INTO training_samples (
            backtest_run_id, fixture_history_id,
            provider_league_id, season, kickoff_at,
            home_team_id, away_team_id,
            market_key, selection,
            home_elo, away_elo, elo_diff, lambda_home, lambda_away,
            model_probability,
            implied_probability, edge, ev_value,
            goals_home, goals_away, actual_outcome, model_correct, split
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            row["backtest_run_id"],
            row["fixture_history_id"],
            row.get("provider_league_id"),
            row.get("season"),
            row.get("kickoff_at"),
            row.get("home_team_id"),
            row.get("away_team_id"),
            row["market_key"],
            row["selection"],
            row.get("home_elo"),
            row.get("away_elo"),
            row.get("elo_diff"),
            row.get("lambda_home"),
            row.get("lambda_away"),
            row.get("model_probability"),
            row.get("implied_probability"),
            row.get("edge"),
            row.get("ev_value"),
            row.get("goals_home"),
            row.get("goals_away"),
            row.get("actual_outcome"),
            row.get("model_correct"),
            row.get("split", "test"),
        ],
    )


def get_team_goals_stats_weighted(
    conn: duckdb.DuckDBPyConnection,
    provider_league_id: int,
    seasons: list[int],
    *,
    recent_weight: float = 0.6,
) -> dict[int, dict]:
    """Like get_team_goals_stats but weights the most recent season higher.

    recent_weight applies to the last season in the list.
    (1 - recent_weight) is applied to all prior seasons combined.

    Falls back to flat get_team_goals_stats when:
      - only one season available (nothing to blend)
      - recent_weight == 0.5 (equal weighting, mathematically identical)

    Captures team form drift from manager changes, injuries, promotion/relegation.
    """
    if len(seasons) <= 1 or recent_weight == 0.5:
        return get_team_goals_stats(conn, provider_league_id, seasons)

    recent = get_team_goals_stats(conn, provider_league_id, seasons[-1:])
    older  = get_team_goals_stats(conn, provider_league_id, seasons[:-1])

    w = recent_weight
    blended: dict[int, dict] = {}
    for tid in set(recent) | set(older):
        r = recent.get(tid)
        o = older.get(tid)
        if r and o:
            blended[tid] = {
                "home_scored":   w * r.get("home_scored",   0.0) + (1 - w) * o.get("home_scored",   0.0),
                "home_conceded": w * r.get("home_conceded", 0.0) + (1 - w) * o.get("home_conceded", 0.0),
                "home_games":    r.get("home_games", 0) + o.get("home_games", 0),
                "away_scored":   w * r.get("away_scored",   0.0) + (1 - w) * o.get("away_scored",   0.0),
                "away_conceded": w * r.get("away_conceded", 0.0) + (1 - w) * o.get("away_conceded", 0.0),
                "away_games":    r.get("away_games", 0) + o.get("away_games", 0),
            }
        elif r:
            blended[tid] = {**_empty_team_stats(), **r}
        else:
            blended[tid] = {**_empty_team_stats(), **o}  # type: ignore[arg-type]
    return blended


def get_current_elo_state(
    conn: duckdb.DuckDBPyConnection,
    entity_scope: str,
) -> dict[int, float]:
    """Return the most recent elo_after for every team in team_elo_history.

    Used by shadow_service to seed current Elo ratings without re-running
    the full backtest. Returns {} if no Elo rows exist yet.
    """
    rows = conn.execute(
        """
        SELECT team_id, elo_after
        FROM (
            SELECT team_id, elo_after,
                   ROW_NUMBER() OVER (
                       PARTITION BY team_id
                       ORDER BY kickoff_at DESC
                   ) AS rn
            FROM team_elo_history
            WHERE entity_scope = ?
        ) t
        WHERE rn = 1
        """,
        [entity_scope],
    ).fetchall()
    return {r[0]: r[1] for r in rows}


# ── Phase 6: shadow value picks queries ──────────────────────────────────────


def get_shadow_value_picks(
    conn: duckdb.DuckDBPyConnection,
    *,
    run_date: str | None = None,
    provider_league_id: int | None = None,
    decision_status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return shadow_value_picks rows for reporting and grading.

    Args:
        run_date:           Filter by exact run_date (ISO string).
        provider_league_id: Filter by league.
        decision_status:    'selected' | 'rejected_cap' | 'rejected_filter' | None (all).
        limit:              Maximum rows returned (default 100).
    """
    filters = []
    params: list = []
    if run_date is not None:
        filters.append("run_date = ?")
        params.append(run_date)
    if provider_league_id is not None:
        filters.append("provider_league_id = ?")
        params.append(provider_league_id)
    if decision_status is not None:
        filters.append("decision_status = ?")
        params.append(decision_status)

    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    rows = conn.execute(
        f"""
        SELECT id, run_date, fixture_id, provider_league_id,
               market_key, selection,
               p_raw, p_cal, p_adj, fair_odds,
               w_rel, risk_score, quality_score, decision_status
        FROM shadow_value_picks
        {where}
        ORDER BY run_date DESC, quality_score DESC
        LIMIT {limit}
        """,
        params,
    ).fetchall()
    return [
        {
            "id":                r[0],
            "run_date":          r[1],
            "fixture_id":        r[2],
            "provider_league_id": r[3],
            "market_key":        r[4],
            "selection":         r[5],
            "p_raw":             r[6],
            "p_cal":             r[7],
            "p_adj":             r[8],
            "fair_odds":         r[9],
            "w_rel":             r[10],
            "risk_score":        r[11],
            "quality_score":     r[12],
            "decision_status":   r[13],
        }
        for r in rows
    ]


# ── Phase 5.5: historical odds queries ───────────────────────────────────────


def get_best_odds_for_fixture(
    conn: duckdb.DuckDBPyConnection,
    fixture_history_id: int,
    market_key: str,
    selection: str,
) -> float | None:
    """Return the best (highest) available odd for a fixture/market/selection.

    Queries odds_history populated via backfill_history_api (Phase 5.5).
    Returns None if no odds are recorded yet.
    """
    row = conn.execute(
        """
        SELECT MAX(odd)
        FROM odds_history
        WHERE fixture_history_id = ?
          AND market_key = ?
          AND selection = ?
          AND odd IS NOT NULL
        """,
        [fixture_history_id, market_key, selection],
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def get_market_odds(
    conn: duckdb.DuckDBPyConnection,
    fixture_history_id: int,
    market_key: str,
) -> list[dict[str, Any]]:
    """Return all bookmaker odds for a fixture/market, ordered by selection then odd.

    Queries odds_history. Returns empty list if no odds are recorded.
    """
    rows = conn.execute(
        """
        SELECT selection, bookmaker_name, odd, implied_probability, scope
        FROM odds_history
        WHERE fixture_history_id = ?
          AND market_key = ?
        ORDER BY selection, odd DESC
        """,
        [fixture_history_id, market_key],
    ).fetchall()
    return [
        {
            "selection":          r[0],
            "bookmaker_name":     r[1],
            "odd":                r[2],
            "implied_probability": r[3],
            "scope":              r[4],
        }
        for r in rows
    ]


def delete_training_samples_for_run(
    conn: duckdb.DuckDBPyConnection, run_id: int
) -> int:
    """Delete all training_samples rows for a backtest run.

    Use this to regenerate features for an existing run without changing
    its backtest_runs metadata record.
    Returns the number of rows deleted.
    """
    count = conn.execute(
        "SELECT COUNT(*) FROM training_samples WHERE backtest_run_id = ?",
        [run_id],
    ).fetchone()[0]
    conn.execute(
        "DELETE FROM training_samples WHERE backtest_run_id = ?",
        [run_id],
    )
    return count
