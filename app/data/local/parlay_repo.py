"""DuckDB repository for Phase 8 parlay engine tables.

Tables managed here:
  parlay_candidates   — one row per generated parlay combination
  parlay_legs         — one row per leg of each parlay
  parlay_results      — settlement results (idempotent on parlay_key)
  parlay_risk_rules   — configurable risk rules (seeded by schema migration)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ── Parlay candidates ──────────────────────────────────────────────────────────


def insert_parlay_candidate(conn, row: dict) -> None:
    """Insert a parlay candidate. Silently ignores duplicate parlay_keys."""
    conn.execute(
        """
        INSERT OR IGNORE INTO parlay_candidates
            (parlay_key, date, parlay_type, legs_count, total_odds,
             joint_probability, implied_probability, edge, ev,
             risk_score, correlation_score, confidence_score,
             recommendation_status, rejection_reason, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            row["parlay_key"],
            row.get("date"),
            row.get("parlay_type"),
            row["legs_count"],
            row.get("total_odds"),
            row.get("joint_probability"),
            row.get("implied_probability"),
            row.get("edge"),
            row.get("ev"),
            row.get("risk_score"),
            row.get("correlation_score"),
            row.get("confidence_score"),
            row.get("recommendation_status"),
            row.get("rejection_reason"),
            json.dumps(row.get("metadata_json") or {}),
        ],
    )


def insert_parlay_legs(conn, parlay_key: str, legs: list[dict]) -> None:
    """Insert all legs for a parlay. Idempotent (INSERT OR IGNORE)."""
    for order, leg in enumerate(legs):
        leg_id = f"{parlay_key}_{order}"
        conn.execute(
            """
            INSERT OR IGNORE INTO parlay_legs
                (leg_id, parlay_key, pick_candidate_id, fixture_id,
                 provider_fixture_id, league_id, market_key, selection,
                 odds, p_model, p_cal, edge, quality_score, leg_order, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                leg_id,
                parlay_key,
                leg.get("pick_candidate_id"),
                leg.get("fixture_id"),
                leg.get("provider_fixture_id"),
                leg.get("league_id"),
                leg.get("market_key"),
                leg.get("selection"),
                leg.get("odds"),
                leg.get("p_model"),
                leg.get("p_cal"),
                leg.get("edge"),
                leg.get("quality_score"),
                order,
                json.dumps(leg.get("metadata_json") or {}),
            ],
        )


def get_parlay_candidates(
    conn,
    date: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Return parlay candidates, optionally filtered by date and status."""
    q = "SELECT * FROM parlay_candidates WHERE 1=1"
    params = []
    if date:
        q += " AND date = ?"
        params.append(date)
    if status:
        q += " AND recommendation_status = ?"
        params.append(status)
    q += " ORDER BY ev DESC, confidence_score DESC"
    if limit:
        q += f" LIMIT {int(limit)}"

    rows = conn.execute(q, params).fetchall()
    cols = [
        "parlay_key", "created_at", "date", "parlay_type", "legs_count",
        "total_odds", "joint_probability", "implied_probability", "edge", "ev",
        "risk_score", "correlation_score", "confidence_score",
        "recommendation_status", "rejection_reason", "metadata_json",
    ]
    return [dict(zip(cols, row)) for row in rows]


def get_parlay_by_key(conn, parlay_key: str) -> dict | None:
    """Return a single parlay candidate with its legs."""
    rows = conn.execute(
        "SELECT * FROM parlay_candidates WHERE parlay_key = ?", [parlay_key]
    ).fetchall()
    if not rows:
        return None
    cols = [
        "parlay_key", "created_at", "date", "parlay_type", "legs_count",
        "total_odds", "joint_probability", "implied_probability", "edge", "ev",
        "risk_score", "correlation_score", "confidence_score",
        "recommendation_status", "rejection_reason", "metadata_json",
    ]
    parlay = dict(zip(cols, rows[0]))
    parlay["legs"] = get_parlay_legs(conn, parlay_key)
    return parlay


def get_parlay_legs(conn, parlay_key: str) -> list[dict]:
    """Return all legs for a parlay, ordered by leg_order."""
    rows = conn.execute(
        """
        SELECT leg_id, parlay_key, pick_candidate_id, fixture_id,
               provider_fixture_id, league_id, market_key, selection,
               odds, p_model, p_cal, edge, quality_score, leg_order, metadata_json
        FROM parlay_legs
        WHERE parlay_key = ?
        ORDER BY leg_order
        """,
        [parlay_key],
    ).fetchall()
    cols = [
        "leg_id", "parlay_key", "pick_candidate_id", "fixture_id",
        "provider_fixture_id", "league_id", "market_key", "selection",
        "odds", "p_model", "p_cal", "edge", "quality_score", "leg_order", "metadata_json",
    ]
    return [dict(zip(cols, row)) for row in rows]


# ── Parlay results ─────────────────────────────────────────────────────────────


def upsert_parlay_result(conn, row: dict) -> None:
    """Insert or update a parlay settlement result."""
    conn.execute(
        """
        INSERT INTO parlay_results
            (parlay_key, result_status, legs_won, legs_lost, legs_void,
             stake_units, profit_units, roi, settled_at, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (parlay_key) DO UPDATE SET
            result_status = excluded.result_status,
            legs_won      = excluded.legs_won,
            legs_lost     = excluded.legs_lost,
            legs_void     = excluded.legs_void,
            profit_units  = excluded.profit_units,
            roi           = excluded.roi,
            settled_at    = excluded.settled_at,
            metadata_json = excluded.metadata_json
        """,
        [
            row["parlay_key"],
            row.get("result_status", "pending"),
            row.get("legs_won", 0),
            row.get("legs_lost", 0),
            row.get("legs_void", 0),
            row.get("stake_units", 0.25),
            row.get("profit_units"),
            row.get("roi"),
            row.get("settled_at", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
            json.dumps(row.get("metadata_json") or {}),
        ],
    )


def get_parlay_results(conn, days: int = 30) -> list[dict]:
    """Return settled parlay results ordered by settled_at DESC."""
    rows = conn.execute(
        """
        SELECT pr.parlay_key, pr.result_status, pr.legs_won, pr.legs_lost,
               pr.legs_void, pr.stake_units, pr.profit_units, pr.roi,
               pr.settled_at, pc.date, pc.parlay_type, pc.legs_count,
               pc.total_odds, pc.ev, pc.edge
        FROM parlay_results pr
        JOIN parlay_candidates pc ON pc.parlay_key = pr.parlay_key
        WHERE pr.result_status != 'pending'
        ORDER BY pr.settled_at DESC
        LIMIT 100
        """
    ).fetchall()
    cols = [
        "parlay_key", "result_status", "legs_won", "legs_lost", "legs_void",
        "stake_units", "profit_units", "roi", "settled_at", "date", "parlay_type",
        "legs_count", "total_odds", "ev", "edge",
    ]
    return [dict(zip(cols, row)) for row in rows]


def get_recent_parlay_summary(conn, days: int = 30) -> dict:
    """Return aggregate performance summary for settled parlays."""
    rows = conn.execute(
        """
        SELECT result_status, profit_units, stake_units
        FROM parlay_results
        WHERE result_status != 'pending'
        """
    ).fetchall()
    wins   = [r for r in rows if r[0] == "win"]
    losses = [r for r in rows if r[0] == "loss"]
    voids  = [r for r in rows if r[0] == "void"]
    settled = wins + losses
    total_profit = sum(float(r[1] or 0) for r in rows if r[0] in ("win", "loss"))
    total_staked = sum(float(r[2] or 0.25) for r in settled)
    roi = (total_profit / total_staked * 100) if total_staked else 0.0
    return {
        "total":          len(rows),
        "wins":           len(wins),
        "losses":         len(losses),
        "voids":          len(voids),
        "settled":        len(settled),
        "profit_units":   round(total_profit, 4),
        "roi_pct":        round(roi, 2),
    }


# ── Counts ─────────────────────────────────────────────────────────────────────


def parlay_counts(conn) -> dict[str, int]:
    """Return row counts for all parlay tables."""
    tables = [
        "parlay_candidates", "parlay_legs", "parlay_results", "parlay_risk_rules"
    ]
    counts: dict[str, int] = {}
    for table in tables:
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            counts[table] = int(row[0]) if row else 0
        except Exception:
            counts[table] = -1
    return counts
