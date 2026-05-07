"""Phase 14: Repository functions for bankroll, stake sizing and risk portfolio."""

from __future__ import annotations

import json
import logging
from datetime import date

logger = logging.getLogger(__name__)


# ── Bankroll profiles ─────────────────────────────────────────────────────────

def upsert_bankroll_profile(conn, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO bankroll_profiles
            (profile_name, bankroll_units, base_unit_size, max_daily_risk_units,
             max_pick_risk_units, max_parlay_risk_units, max_same_league_units,
             max_same_market_units, max_same_team_units, kelly_fraction,
             min_edge_for_stake, min_confidence_for_stake, is_active, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (profile_name) DO UPDATE SET
            bankroll_units           = excluded.bankroll_units,
            base_unit_size           = excluded.base_unit_size,
            max_daily_risk_units     = excluded.max_daily_risk_units,
            max_pick_risk_units      = excluded.max_pick_risk_units,
            max_parlay_risk_units    = excluded.max_parlay_risk_units,
            max_same_league_units    = excluded.max_same_league_units,
            max_same_market_units    = excluded.max_same_market_units,
            max_same_team_units      = excluded.max_same_team_units,
            kelly_fraction           = excluded.kelly_fraction,
            min_edge_for_stake       = excluded.min_edge_for_stake,
            min_confidence_for_stake = excluded.min_confidence_for_stake,
            is_active                = excluded.is_active,
            metadata_json            = excluded.metadata_json,
            updated_at               = now()
        """,
        [
            row.get("profile_name", "default"),
            row.get("bankroll_units", 100.0),
            row.get("base_unit_size", 1.0),
            row.get("max_daily_risk_units", 5.0),
            row.get("max_pick_risk_units", 1.5),
            row.get("max_parlay_risk_units", 0.5),
            row.get("max_same_league_units", 3.0),
            row.get("max_same_market_units", 3.0),
            row.get("max_same_team_units", 2.0),
            row.get("kelly_fraction", 0.25),
            row.get("min_edge_for_stake", 0.02),
            row.get("min_confidence_for_stake", 0.52),
            row.get("is_active", True),
            json.dumps(row.get("metadata")) if row.get("metadata") else None,
        ],
    )


def get_active_bankroll_profile(conn) -> dict | None:
    try:
        row = conn.execute(
            """
            SELECT profile_name, bankroll_units, base_unit_size, max_daily_risk_units,
                   max_pick_risk_units, max_parlay_risk_units, max_same_league_units,
                   max_same_market_units, max_same_team_units, kelly_fraction,
                   min_edge_for_stake, min_confidence_for_stake, is_active
            FROM bankroll_profiles
            WHERE is_active = true
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        cols = [
            "profile_name", "bankroll_units", "base_unit_size", "max_daily_risk_units",
            "max_pick_risk_units", "max_parlay_risk_units", "max_same_league_units",
            "max_same_market_units", "max_same_team_units", "kelly_fraction",
            "min_edge_for_stake", "min_confidence_for_stake", "is_active",
        ]
        return dict(zip(cols, row))
    except Exception as exc:
        logger.debug("get_active_bankroll_profile: %s", exc)
        return None


# ── Stake recommendations ─────────────────────────────────────────────────────

def upsert_stake_recommendation(conn, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO stake_recommendations
            (pick_candidate_id, published_pick_id, fixture_id, provider_fixture_id,
             market_key, selection, league_id, team_home_id, team_away_id,
             odds, p_model, edge, ev, ev_adj, strategy_score, strategy_recommendation,
             clv_percent, risk_score, correlation_score, kelly_full, kelly_fractional,
             recommended_units, stake_label, rejection_reason, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (pick_candidate_id) DO UPDATE SET
            published_pick_id       = excluded.published_pick_id,
            risk_score              = excluded.risk_score,
            correlation_score       = excluded.correlation_score,
            kelly_full              = excluded.kelly_full,
            kelly_fractional        = excluded.kelly_fractional,
            recommended_units       = excluded.recommended_units,
            stake_label             = excluded.stake_label,
            rejection_reason        = excluded.rejection_reason,
            strategy_score          = excluded.strategy_score,
            strategy_recommendation = excluded.strategy_recommendation,
            clv_percent             = excluded.clv_percent,
            metadata_json           = excluded.metadata_json,
            created_at              = now()
        """,
        [
            row.get("pick_candidate_id"),
            row.get("published_pick_id"),
            row.get("fixture_id"),
            row.get("provider_fixture_id"),
            row.get("market_key"),
            row.get("selection"),
            row.get("league_id"),
            row.get("team_home_id"),
            row.get("team_away_id"),
            row.get("odds"),
            row.get("p_model"),
            row.get("edge"),
            row.get("ev"),
            row.get("ev_adj"),
            row.get("strategy_score"),
            row.get("strategy_recommendation"),
            row.get("clv_percent"),
            row.get("risk_score"),
            row.get("correlation_score"),
            row.get("kelly_full"),
            row.get("kelly_fractional"),
            row.get("recommended_units"),
            row.get("stake_label"),
            row.get("rejection_reason"),
            json.dumps(row.get("metadata")) if row.get("metadata") else None,
        ],
    )


def get_stake_recommendation(conn, pick_candidate_id: int) -> dict | None:
    try:
        row = conn.execute(
            """
            SELECT pick_candidate_id, market_key, selection, league_id, odds, p_model,
                   edge, strategy_score, strategy_recommendation, clv_percent,
                   risk_score, correlation_score, kelly_full, kelly_fractional,
                   recommended_units, stake_label, rejection_reason
            FROM stake_recommendations WHERE pick_candidate_id = ?
            """,
            [pick_candidate_id],
        ).fetchone()
        if not row:
            return None
        cols = [
            "pick_candidate_id", "market_key", "selection", "league_id", "odds",
            "p_model", "edge", "strategy_score", "strategy_recommendation", "clv_percent",
            "risk_score", "correlation_score", "kelly_full", "kelly_fractional",
            "recommended_units", "stake_label", "rejection_reason",
        ]
        return dict(zip(cols, row))
    except Exception as exc:
        logger.debug("get_stake_recommendation: %s", exc)
        return None


def get_stake_recommendations_by_day(conn, days: int = 1) -> list[dict]:
    try:
        rows = conn.execute(
            """
            SELECT pick_candidate_id, fixture_id, market_key, selection, league_id,
                   odds, p_model, edge, strategy_score, strategy_recommendation,
                   clv_percent, risk_score, correlation_score, recommended_units,
                   stake_label, rejection_reason
            FROM stake_recommendations
            WHERE created_at >= current_timestamp - INTERVAL (?) DAY
            ORDER BY recommended_units DESC
            """,
            [days],
        ).fetchall()
        cols = [
            "pick_candidate_id", "fixture_id", "market_key", "selection", "league_id",
            "odds", "p_model", "edge", "strategy_score", "strategy_recommendation",
            "clv_percent", "risk_score", "correlation_score", "recommended_units",
            "stake_label", "rejection_reason",
        ]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as exc:
        logger.debug("get_stake_recommendations_by_day: %s", exc)
        return []


# ── Portfolio snapshots ───────────────────────────────────────────────────────

def upsert_portfolio_snapshot(conn, row: dict) -> None:
    snap_date = row.get("snapshot_date") or date.today().isoformat()
    conn.execute(
        """
        INSERT INTO portfolio_risk_snapshots
            (snapshot_date, total_picks, total_recommended_units, total_daily_risk_units,
             exposure_by_league_json, exposure_by_market_json, exposure_by_team_json,
             correlated_groups_json, portfolio_score, risk_level, warnings_json, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (snapshot_date) DO UPDATE SET
            total_picks              = excluded.total_picks,
            total_recommended_units  = excluded.total_recommended_units,
            total_daily_risk_units   = excluded.total_daily_risk_units,
            exposure_by_league_json  = excluded.exposure_by_league_json,
            exposure_by_market_json  = excluded.exposure_by_market_json,
            exposure_by_team_json    = excluded.exposure_by_team_json,
            correlated_groups_json   = excluded.correlated_groups_json,
            portfolio_score          = excluded.portfolio_score,
            risk_level               = excluded.risk_level,
            warnings_json            = excluded.warnings_json,
            metadata_json            = excluded.metadata_json,
            created_at               = now()
        """,
        [
            snap_date,
            row.get("total_picks", 0),
            row.get("total_recommended_units", 0.0),
            row.get("total_daily_risk_units", 0.0),
            json.dumps(row.get("exposure_by_league") or {}),
            json.dumps(row.get("exposure_by_market") or {}),
            json.dumps(row.get("exposure_by_team") or {}),
            json.dumps(row.get("correlated_groups") or []),
            row.get("portfolio_score"),
            row.get("risk_level"),
            json.dumps(row.get("warnings") or []),
            json.dumps(row.get("metadata")) if row.get("metadata") else None,
        ],
    )


def get_latest_portfolio_snapshot(conn) -> dict | None:
    try:
        row = conn.execute(
            """
            SELECT snapshot_date, total_picks, total_recommended_units, total_daily_risk_units,
                   exposure_by_league_json, exposure_by_market_json, exposure_by_team_json,
                   correlated_groups_json, portfolio_score, risk_level, warnings_json
            FROM portfolio_risk_snapshots
            ORDER BY snapshot_date DESC LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        cols = [
            "snapshot_date", "total_picks", "total_recommended_units", "total_daily_risk_units",
            "exposure_by_league_json", "exposure_by_market_json", "exposure_by_team_json",
            "correlated_groups_json", "portfolio_score", "risk_level", "warnings_json",
        ]
        d = dict(zip(cols, row))
        for k in ("exposure_by_league_json", "exposure_by_market_json", "exposure_by_team_json",
                  "correlated_groups_json", "warnings_json"):
            try:
                d[k] = json.loads(d[k]) if d[k] else None
            except Exception:
                pass
        return d
    except Exception as exc:
        logger.debug("get_latest_portfolio_snapshot: %s", exc)
        return None


# ── Risk events ───────────────────────────────────────────────────────────────

def insert_risk_event(conn, row: dict) -> None:
    try:
        conn.execute(
            """
            INSERT INTO risk_events
                (id, event_type, severity, entity_type, entity_id, message, metadata_json)
            VALUES (nextval('risk_events_seq'), ?, ?, ?, ?, ?, ?)
            """,
            [
                row.get("event_type", "unknown"),
                row.get("severity", "low"),
                row.get("entity_type"),
                str(row.get("entity_id")) if row.get("entity_id") else None,
                row.get("message", "")[:500],
                json.dumps(row.get("metadata")) if row.get("metadata") else None,
            ],
        )
    except Exception as exc:
        logger.debug("insert_risk_event: %s", exc)


def get_risk_events(conn, days: int = 7) -> list[dict]:
    try:
        rows = conn.execute(
            """
            SELECT event_type, severity, entity_type, entity_id, message, created_at
            FROM risk_events
            WHERE created_at >= current_timestamp - INTERVAL (?) DAY
            ORDER BY created_at DESC LIMIT 100
            """,
            [days],
        ).fetchall()
        cols = ["event_type", "severity", "entity_type", "entity_id", "message", "created_at"]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as exc:
        logger.debug("get_risk_events: %s", exc)
        return []


# ── Summary ───────────────────────────────────────────────────────────────────

def get_bankroll_summary(conn, days: int = 30) -> dict:
    try:
        r = conn.execute(
            """
            SELECT COUNT(*), AVG(recommended_units), AVG(risk_score),
                   SUM(CASE WHEN recommended_units > 0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN rejection_reason IS NOT NULL THEN 1 ELSE 0 END)
            FROM stake_recommendations
            WHERE created_at >= current_timestamp - INTERVAL (?) DAY
            """,
            [days],
        ).fetchone()
        total = r[0] or 0
        return {
            "total_recommendations": total,
            "avg_recommended_units": round(r[1], 3) if r[1] else None,
            "avg_risk_score":        round(r[2], 1) if r[2] else None,
            "with_stake":            r[3] or 0,
            "rejected":              r[4] or 0,
        }
    except Exception as exc:
        return {"error": str(exc)}
