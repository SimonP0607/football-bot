"""Phase 16: Dashboard data layer — all DuckDB reads centralised here.

All public functions are read-only and safe: missing tables return a
sensible default rather than raising.  No writes to DuckDB, Supabase,
or the API-Football service are performed from this module.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

logger = logging.getLogger(__name__)

# ── Connection ─────────────────────────────────────────────────────────────────

def get_connection() -> duckdb.DuckDBPyConnection:
    """Open a fresh read-only connection to the local DuckDB file.

    Uses the LOCAL_DB_PATH from config.py.  Opens in read_only mode so the
    dashboard never blocks the bot's writer connection.
    """
    from app.core.config import settings
    path = Path(settings.local_db_path)
    if not path.exists():
        raise FileNotFoundError(f"DuckDB not found: {path.resolve()}")
    return duckdb.connect(str(path), read_only=True)


# ── Low-level helpers ──────────────────────────────────────────────────────────

def table_exists(conn: duckdb.DuckDBPyConnection, table: str) -> bool:
    try:
        r = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='main' AND table_name=?",
            [table],
        ).fetchone()
        return bool(r and r[0] > 0)
    except Exception:
        return False


def get_table_count(conn: duckdb.DuckDBPyConnection, table: str) -> int | None:
    """Return row count for a table, or None if the table does not exist."""
    if not table_exists(conn, table):
        return None
    try:
        r = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return r[0] if r else 0
    except Exception:
        return None


def safe_query(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
    params: list | None = None,
    default: Any = None,
) -> Any:
    """Execute sql, return result or *default* on any error."""
    try:
        result = conn.execute(sql, params or [])
        return result
    except Exception as exc:
        logger.debug("safe_query failed: %s — %s", sql[:80], exc)
        return None


def safe_df(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
    params: list | None = None,
) -> pd.DataFrame:
    """Return a DataFrame or empty DataFrame on error."""
    try:
        return conn.execute(sql, params or []).df()
    except Exception as exc:
        logger.debug("safe_df failed: %s — %s", sql[:80], exc)
        return pd.DataFrame()


def get_recent_rows(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    limit: int = 50,
    order_col: str = "created_at",
) -> pd.DataFrame:
    if not table_exists(conn, table):
        return pd.DataFrame()
    try:
        # Check if order_col exists
        cols = [r[0] for r in conn.execute(
            f"SELECT column_name FROM information_schema.columns "
            f"WHERE table_name='{table}'"
        ).fetchall()]
        order = f"ORDER BY {order_col} DESC" if order_col in cols else ""
        return conn.execute(f"SELECT * FROM {table} {order} LIMIT {limit}").df()
    except Exception as exc:
        logger.debug("get_recent_rows(%s): %s", table, exc)
        return pd.DataFrame()


def list_all_tables(conn: duckdb.DuckDBPyConnection) -> list[str]:
    try:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='main' ORDER BY table_name"
        ).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


# ── Overview data ──────────────────────────────────────────────────────────────

def get_overview(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    today = date.today().isoformat()
    out: dict[str, Any] = {"date": today}

    # Fixtures today (Supabase-synced, stored in fixtures table if exists)
    out["fixtures_today"] = None  # fixtures are in Supabase, not DuckDB by default

    # Pick candidates today
    if table_exists(conn, "pick_candidates"):
        r = conn.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN status='selected' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) "
            "FROM pick_candidates "
            "WHERE DATE(created_at)=?", [today]
        ).fetchone()
        if r:
            out["candidates_total"] = r[0] or 0
            out["candidates_selected"] = r[1] or 0
            out["candidates_rejected"] = r[2] or 0
    else:
        out["candidates_total"] = None

    # Shadow value picks
    if table_exists(conn, "shadow_value_picks"):
        r = conn.execute(
            "SELECT COUNT(*) FROM shadow_value_picks WHERE DATE(created_at)=?", [today]
        ).fetchone()
        out["shadow_picks_today"] = r[0] if r else 0
    else:
        out["shadow_picks_today"] = None

    # Pick results pending settlement
    if table_exists(conn, "pick_results"):
        r = conn.execute(
            "SELECT COUNT(*) FROM pick_results WHERE result IS NULL OR result='pending'"
        ).fetchone()
        out["pending_settlement"] = r[0] if r else 0
    elif table_exists(conn, "pick_learning_annotations"):
        r = conn.execute(
            "SELECT COUNT(*) FROM pick_learning_annotations "
            "WHERE result_status='pending' OR result_status IS NULL"
        ).fetchone()
        out["pending_settlement"] = r[0] if r else 0
    else:
        out["pending_settlement"] = None

    # Performance summary (30d)
    perf = get_performance_summary(conn, days=30)
    out["perf_30d"] = perf

    # Last sync
    if table_exists(conn, "api_sync_runs"):
        r = conn.execute(
            "SELECT MAX(created_at) FROM api_sync_runs"
        ).fetchone()
        out["last_sync"] = str(r[0]) if r and r[0] else None
    else:
        out["last_sync"] = None

    # Governance
    if table_exists(conn, "activation_recommendations"):
        rows = conn.execute(
            "SELECT module, recommendation FROM activation_recommendations ORDER BY module"
        ).fetchall()
        out["governance_recs"] = {r[0]: r[1] for r in rows}
    else:
        out["governance_recs"] = {}

    # Active experiments
    if table_exists(conn, "experiment_registry"):
        r = conn.execute(
            "SELECT COUNT(*) FROM experiment_registry WHERE status='active'"
        ).fetchone()
        out["active_experiments"] = r[0] if r else 0
    else:
        out["active_experiments"] = 0

    # DuckDB table count
    out["duckdb_table_count"] = len(list_all_tables(conn))

    # Scheduler last run
    if table_exists(conn, "scheduler_runs"):
        r = conn.execute(
            "SELECT job_key, MAX(started_at) FROM scheduler_runs GROUP BY job_key"
        ).fetchall()
        out["scheduler_last"] = {row[0]: str(row[1]) for row in r if row[1]}
    else:
        out["scheduler_last"] = {}

    return out


# ── Picks data ─────────────────────────────────────────────────────────────────

def get_picks_today(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    today = date.today().isoformat()
    if not table_exists(conn, "shadow_value_picks"):
        return pd.DataFrame()
    cols_raw = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='shadow_value_picks' ORDER BY ordinal_position"
    ).fetchall()
    cols = {r[0] for r in cols_raw}

    want = ["id", "fixture_id", "market_key", "selection", "status",
            "edge", "offered_odds", "p_cal", "p_implied",
            "confidence", "quality_score", "value_engine_status",
            "rejection_reason", "ev_adj", "run_date", "league_name",
            "home_provider_team_id", "away_provider_team_id"]
    select = ", ".join(c for c in want if c in cols)
    if not select:
        return pd.DataFrame()
    return safe_df(
        conn,
        f"SELECT {select} FROM shadow_value_picks WHERE DATE(created_at)=? ORDER BY quality_score DESC NULLS LAST",
        [today],
    )


def get_picks_candidates(conn: duckdb.DuckDBPyConnection, days: int = 1) -> pd.DataFrame:
    if not table_exists(conn, "pick_candidates"):
        return pd.DataFrame()
    return safe_df(
        conn,
        "SELECT * FROM pick_candidates "
        "WHERE created_at >= current_timestamp - INTERVAL (?) DAY "
        "ORDER BY created_at DESC LIMIT 200",
        [days],
    )


def get_picks_with_stake(conn: duckdb.DuckDBPyConnection, days: int = 7) -> pd.DataFrame:
    if not table_exists(conn, "stake_recommendations"):
        return pd.DataFrame()
    return safe_df(
        conn,
        "SELECT * FROM stake_recommendations "
        "WHERE created_at >= current_timestamp - INTERVAL (?) DAY "
        "ORDER BY created_at DESC",
        [days],
    )


# ── Performance data ───────────────────────────────────────────────────────────

def get_performance_summary(conn: duckdb.DuckDBPyConnection, days: int = 30) -> dict:
    out: dict = {"days": days}
    if table_exists(conn, "pick_learning_annotations"):
        r = conn.execute(
            """
            SELECT COUNT(*),
                   SUM(CASE WHEN result_status='win' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN result_status='loss' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN result_status='void' THEN 1 ELSE 0 END),
                   SUM(profit), AVG(profit)
            FROM pick_learning_annotations
            WHERE created_at >= current_timestamp - INTERVAL (?) DAY
              AND result_status IN ('win','loss','void')
            """,
            [days],
        ).fetchone()
        if r and r[0]:
            wins, losses = (r[1] or 0), (r[2] or 0)
            n = wins + losses
            out["total"] = r[0]; out["wins"] = wins; out["losses"] = losses
            out["voids"] = r[3] or 0
            out["hit_rate"] = round(wins / n, 4) if n else None
            out["total_profit"] = round(r[4], 3) if r[4] is not None else None
            out["roi"] = round(r[4] / n, 4) if n and r[4] is not None else None
        else:
            out["total"] = 0
    return out


def get_performance_by_market(conn: duckdb.DuckDBPyConnection, days: int = 30) -> pd.DataFrame:
    if not table_exists(conn, "pick_learning_annotations"):
        return pd.DataFrame()
    return safe_df(conn, """
        SELECT market_key,
               COUNT(*) AS picks,
               SUM(CASE WHEN result_status='win' THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN result_status='loss' THEN 1 ELSE 0 END) AS losses,
               ROUND(AVG(profit),3) AS avg_profit,
               ROUND(SUM(profit),3) AS total_profit
        FROM pick_learning_annotations
        WHERE created_at >= current_timestamp - INTERVAL (?) DAY
          AND result_status IN ('win','loss','void')
        GROUP BY market_key ORDER BY total_profit DESC NULLS LAST
    """, [days])


def get_performance_by_league(conn: duckdb.DuckDBPyConnection, days: int = 30) -> pd.DataFrame:
    if not table_exists(conn, "pick_learning_annotations"):
        return pd.DataFrame()
    return safe_df(conn, """
        SELECT league_id,
               COUNT(*) AS picks,
               SUM(CASE WHEN result_status='win' THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN result_status='loss' THEN 1 ELSE 0 END) AS losses,
               ROUND(SUM(profit),3) AS total_profit
        FROM pick_learning_annotations
        WHERE created_at >= current_timestamp - INTERVAL (?) DAY
          AND result_status IN ('win','loss','void')
        GROUP BY league_id ORDER BY total_profit DESC NULLS LAST
        LIMIT 20
    """, [days])


def get_cumulative_profit(conn: duckdb.DuckDBPyConnection, days: int = 90) -> pd.DataFrame:
    if not table_exists(conn, "pick_learning_annotations"):
        return pd.DataFrame()
    df = safe_df(conn, """
        SELECT DATE(created_at) AS day, SUM(profit) AS daily_profit
        FROM pick_learning_annotations
        WHERE created_at >= current_timestamp - INTERVAL (?) DAY
          AND result_status IN ('win','loss') AND profit IS NOT NULL
        GROUP BY DATE(created_at) ORDER BY day
    """, [days])
    if df.empty:
        return df
    df["cumulative_profit"] = df["daily_profit"].cumsum()
    return df


# ── Governance data ────────────────────────────────────────────────────────────

def get_governance_summary(conn: duckdb.DuckDBPyConnection) -> dict:
    out: dict = {}
    if table_exists(conn, "experiment_registry"):
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM experiment_registry GROUP BY status"
        ).fetchall()
        out["experiments_by_status"] = {r[0]: r[1] for r in rows}
    if table_exists(conn, "activation_recommendations"):
        df = safe_df(conn,
            "SELECT module, recommendation, sample_size, gates_passed, gates_total, "
            "roi, lift_vs_baseline, blocking_reason FROM activation_recommendations ORDER BY module"
        )
        out["activation_df"] = df
    if table_exists(conn, "experiment_results"):
        df = safe_df(conn,
            "SELECT experiment_key, days_window, sample_size, roi, lift_vs_baseline, "
            "gates_passed, gates_total, recommendation "
            "FROM experiment_results WHERE days_window=30 ORDER BY experiment_key"
        )
        out["results_df"] = df
    if table_exists(conn, "model_decision_audit"):
        r = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN decision_changed THEN 1 ELSE 0 END) "
            "FROM model_decision_audit WHERE created_at >= current_timestamp - INTERVAL 7 DAY"
        ).fetchone()
        out["audit_7d_total"] = r[0] or 0 if r else 0
        out["audit_7d_changed"] = r[1] or 0 if r else 0
    return out


def get_experiment_registry(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return get_recent_rows(conn, "experiment_registry", 20, "created_at")


# ── Bankroll data ──────────────────────────────────────────────────────────────

def get_bankroll_summary(conn: duckdb.DuckDBPyConnection) -> dict:
    out: dict = {}
    if table_exists(conn, "bankroll_profiles"):
        r = conn.execute(
            "SELECT profile_name, bankroll_units, kelly_fraction, max_pick_risk_units "
            "FROM bankroll_profiles WHERE is_active=true LIMIT 1"
        ).fetchone()
        if r:
            out["profile"] = {
                "name": r[0], "units": r[1],
                "kelly_frac": r[2], "max_risk": r[3],
            }
    if table_exists(conn, "portfolio_risk_snapshots"):
        r = conn.execute(
            "SELECT snapshot_date, portfolio_score, risk_level, total_picks, "
            "total_recommended_units, warnings_json "
            "FROM portfolio_risk_snapshots ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if r:
            out["snapshot"] = {
                "date": str(r[0]), "score": r[1], "level": r[2],
                "picks": r[3], "units": r[4], "warnings": r[5],
            }
    if table_exists(conn, "stake_recommendations"):
        r = conn.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN stake_label!='no_stake' THEN 1 ELSE 0 END), "
            "AVG(recommended_units) "
            "FROM stake_recommendations "
            "WHERE DATE(created_at)=?", [date.today().isoformat()]
        ).fetchone()
        out["today_stakes"] = {
            "total": r[0] or 0 if r else 0,
            "with_stake": r[1] or 0 if r else 0,
            "avg_units": round(r[2], 3) if r and r[2] else None,
        }
    if table_exists(conn, "risk_events"):
        df = safe_df(conn,
            "SELECT * FROM risk_events ORDER BY created_at DESC LIMIT 20"
        )
        out["risk_events_df"] = df
    return out


def get_stake_recommendations_df(conn: duckdb.DuckDBPyConnection, days: int = 7) -> pd.DataFrame:
    return get_recent_rows(conn, "stake_recommendations", 100, "created_at")


def get_portfolio_history(conn: duckdb.DuckDBPyConnection, days: int = 30) -> pd.DataFrame:
    if not table_exists(conn, "portfolio_risk_snapshots"):
        return pd.DataFrame()
    return safe_df(conn,
        "SELECT snapshot_date, portfolio_score, risk_level, total_picks, total_recommended_units "
        "FROM portfolio_risk_snapshots "
        "WHERE created_at >= current_timestamp - INTERVAL (?) DAY "
        "ORDER BY snapshot_date",
        [days],
    )


# ── Parlay data ────────────────────────────────────────────────────────────────

def get_parlay_summary(conn: duckdb.DuckDBPyConnection) -> dict:
    out: dict = {}
    if table_exists(conn, "parlay_candidates"):
        r = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN status='recommended' THEN 1 ELSE 0 END), "
            "AVG(total_odds), AVG(ev) "
            "FROM parlay_candidates WHERE created_at >= current_timestamp - INTERVAL 30 DAY"
        ).fetchone()
        if r:
            out["total_30d"] = r[0] or 0
            out["recommended_30d"] = r[1] or 0
            out["avg_odds"] = round(r[2], 2) if r[2] else None
            out["avg_ev"] = round(r[3], 4) if r[3] else None
    return out


def get_parlays_df(conn: duckdb.DuckDBPyConnection, days: int = 30) -> pd.DataFrame:
    if not table_exists(conn, "parlay_candidates"):
        return pd.DataFrame()
    return safe_df(conn,
        "SELECT parlay_key, DATE(created_at) AS date, n_legs, status, "
        "total_odds, joint_prob, ev, edge, correlation_score, risk_label "
        "FROM parlay_candidates "
        "WHERE created_at >= current_timestamp - INTERVAL (?) DAY "
        "ORDER BY created_at DESC LIMIT 100",
        [days],
    )


def get_parlay_results_df(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return get_recent_rows(conn, "parlay_results", 50, "created_at")


# ── API Budget data ────────────────────────────────────────────────────────────

def get_api_usage_summary(conn: duckdb.DuckDBPyConnection) -> dict:
    out: dict = {}
    today = date.today().isoformat()
    if table_exists(conn, "api_usage_snapshots"):
        r = conn.execute(
            "SELECT SUM(requests_used), MAX(updated_at) "
            "FROM api_usage_snapshots WHERE DATE(created_at)=?", [today]
        ).fetchone()
        out["requests_today"] = r[0] or 0 if r else 0
        out["last_update"] = str(r[1]) if r and r[1] else None
    if table_exists(conn, "api_sync_runs"):
        r = conn.execute(
            "SELECT COUNT(*), SUM(api_calls_used) FROM api_sync_runs "
            "WHERE DATE(created_at)=?", [today]
        ).fetchone()
        out["runs_today"] = r[0] or 0 if r else 0
        out["calls_today"] = r[1] or 0 if r else 0
    return out


def get_api_usage_df(conn: duckdb.DuckDBPyConnection, days: int = 7) -> pd.DataFrame:
    if table_exists(conn, "api_usage_snapshots"):
        return safe_df(conn,
            "SELECT * FROM api_usage_snapshots "
            "WHERE created_at >= current_timestamp - INTERVAL (?) DAY "
            "ORDER BY created_at DESC LIMIT 100",
            [days],
        )
    return pd.DataFrame()


def get_sync_runs_df(conn: duckdb.DuckDBPyConnection, days: int = 3) -> pd.DataFrame:
    if not table_exists(conn, "api_sync_runs"):
        return pd.DataFrame()
    return safe_df(conn,
        "SELECT * FROM api_sync_runs "
        "WHERE created_at >= current_timestamp - INTERVAL (?) DAY "
        "ORDER BY created_at DESC LIMIT 50",
        [days],
    )


# ── Live & Prematch data ───────────────────────────────────────────────────────

def get_live_snapshots(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    if not table_exists(conn, "live_fixture_snapshots"):
        return pd.DataFrame()
    return safe_df(conn,
        "SELECT * FROM live_fixture_snapshots "
        "WHERE updated_at >= current_timestamp - INTERVAL 6 HOUR "
        "ORDER BY updated_at DESC LIMIT 50"
    )


def get_prematch_intelligence(conn: duckdb.DuckDBPyConnection, days: int = 2) -> pd.DataFrame:
    for tbl in ["prematch_intelligence", "prematch_signals"]:
        if table_exists(conn, tbl):
            return safe_df(conn,
                f"SELECT * FROM {tbl} "
                f"WHERE created_at >= current_timestamp - INTERVAL (?) DAY "
                f"ORDER BY created_at DESC LIMIT 50",
                [days],
            )
    return pd.DataFrame()


def get_market_movement_signals(conn: duckdb.DuckDBPyConnection, days: int = 3) -> pd.DataFrame:
    if not table_exists(conn, "market_movement_signals"):
        return pd.DataFrame()
    return safe_df(conn,
        "SELECT * FROM market_movement_signals "
        "WHERE created_at >= current_timestamp - INTERVAL (?) DAY "
        "ORDER BY created_at DESC LIMIT 100",
        [days],
    )


def get_pick_clv_recent(conn: duckdb.DuckDBPyConnection, days: int = 30) -> pd.DataFrame:
    if not table_exists(conn, "pick_clv_results"):
        return pd.DataFrame()
    return safe_df(conn,
        "SELECT market_key, clv_percent, beat_closing_line, clv_result, created_at "
        "FROM pick_clv_results "
        "WHERE created_at >= current_timestamp - INTERVAL (?) DAY "
        "ORDER BY created_at DESC LIMIT 100",
        [days],
    )


# ── Entities & Players data ────────────────────────────────────────────────────

def search_players(conn: duckdb.DuckDBPyConnection, query: str, limit: int = 20) -> pd.DataFrame:
    if not table_exists(conn, "player_season_profiles"):
        return pd.DataFrame()
    like = f"%{query.lower()}%"
    return safe_df(conn,
        "SELECT player_id, player_name, team_id, league_id, season, "
        "goals, assists, avg_minutes, avg_rating "
        "FROM player_season_profiles WHERE LOWER(player_name) LIKE ? "
        "ORDER BY avg_rating DESC NULLS LAST LIMIT ?",
        [like, limit],
    )


def get_player_signals(conn: duckdb.DuckDBPyConnection, player_id: int | None = None) -> pd.DataFrame:
    if not table_exists(conn, "player_prop_signals"):
        return pd.DataFrame()
    if player_id:
        return safe_df(conn,
            "SELECT * FROM player_prop_signals WHERE player_id=? ORDER BY created_at DESC LIMIT 20",
            [player_id],
        )
    return safe_df(conn,
        "SELECT * FROM player_prop_signals ORDER BY created_at DESC LIMIT 50"
    )


def get_hot_players(conn: duckdb.DuckDBPyConnection, limit: int = 20) -> pd.DataFrame:
    if not table_exists(conn, "player_prop_signals"):
        return pd.DataFrame()
    return safe_df(conn,
        "SELECT player_id, market_key, status, confidence_score, trend_score, created_at "
        "FROM player_prop_signals WHERE status='recommended' "
        "ORDER BY confidence_score DESC NULLS LAST LIMIT ?",
        [limit],
    )


def get_team_list(conn: duckdb.DuckDBPyConnection, query: str = "", limit: int = 30) -> pd.DataFrame:
    for tbl in ["entity_catalog", "team_elo_history"]:
        if table_exists(conn, tbl):
            if tbl == "entity_catalog":
                where = "WHERE LOWER(entity_name) LIKE ?" if query else ""
                params = [f"%{query.lower()}%", limit] if query else [limit]
                return safe_df(conn,
                    f"SELECT * FROM {tbl} {where} ORDER BY entity_name LIMIT ?", params
                )
            else:
                return safe_df(conn,
                    "SELECT DISTINCT team_id FROM team_elo_history ORDER BY team_id LIMIT ?",
                    [limit],
                )
    return pd.DataFrame()


# ── System Health data ─────────────────────────────────────────────────────────

def get_all_table_counts(conn: duckdb.DuckDBPyConnection) -> dict[str, int | None]:
    tables = list_all_tables(conn)
    return {t: get_table_count(conn, t) for t in tables}


def get_scheduler_runs(conn: duckdb.DuckDBPyConnection, limit: int = 30) -> pd.DataFrame:
    return get_recent_rows(conn, "scheduler_runs", limit, "started_at")


def get_config_summary() -> dict:
    """Return config values with secrets masked."""
    try:
        from app.core.config import settings

        def _mask(v: str) -> str:
            if not v:
                return "(not set)"
            if len(v) <= 8:
                return "***"
            return v[:4] + "***" + v[-2:]

        return {
            "APP_ENV": settings.app_env,
            "LOG_LEVEL": settings.log_level,
            "LOCAL_DB_PATH": settings.local_db_path,
            "TELEGRAM_BOT_TOKEN": _mask(settings.telegram_bot_token),
            "VALUE_ENGINE_ENABLED": getattr(settings, "value_engine_enabled", "?"),
            "STRATEGY_LEARNING_ENABLED": getattr(settings, "strategy_learning_enabled", "?"),
            "BANKROLL_ENGINE_ENABLED": getattr(settings, "bankroll_engine_enabled", "?"),
            "MARKET_INTELLIGENCE_ENABLED": getattr(settings, "market_intelligence_enabled", "?"),
            "MODEL_GOVERNANCE_ENABLED": getattr(settings, "model_governance_enabled", "?"),
            "SCHEDULER_ENABLED": getattr(settings, "scheduler_enabled", "?"),
            "AI_ROUTER_ENABLED": getattr(settings, "ai_router_enabled", "?"),
        }
    except Exception as exc:
        return {"error": str(exc)}


def get_strategy_profiles(conn: duckdb.DuckDBPyConnection, limit: int = 20) -> pd.DataFrame:
    if not table_exists(conn, "strategy_profiles"):
        return pd.DataFrame()
    return safe_df(conn,
        "SELECT strategy_key, market_key, scope, sample_size, hit_rate, roi, "
        "strategy_score, recommendation "
        "FROM strategy_profiles ORDER BY strategy_score DESC NULLS LAST LIMIT ?",
        [limit],
    )
