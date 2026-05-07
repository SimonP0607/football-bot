"""Phase 13: Strategy Learning repository — DuckDB read/write for strategy profiles,
annotations, adjustments and learning run logs."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)


# ── Upserts ───────────────────────────────────────────────────────────────────

def upsert_strategy_profile(conn: "duckdb.DuckDBPyConnection", row: dict) -> bool:
    """Insert or update a strategy profile. Returns True on success."""
    try:
        conn.execute(
            """
            INSERT INTO strategy_profiles (
                id, created_at, updated_at,
                strategy_key, market_key, league_id, league_name, scope,
                odds_bucket, confidence_bucket, edge_bucket, clv_bucket,
                sample_size, wins, losses, voids,
                hit_rate, roi, avg_profit,
                avg_clv_percent, clv_beat_rate,
                avg_edge, avg_quality_score, avg_confidence,
                stability_score, strategy_score, recommendation, metadata_json
            ) VALUES (
                nextval('strategy_profiles_seq'),
                now(), now(),
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?
            )
            ON CONFLICT (strategy_key) DO UPDATE SET
                updated_at        = now(),
                market_key        = excluded.market_key,
                league_id         = excluded.league_id,
                league_name       = excluded.league_name,
                scope             = excluded.scope,
                odds_bucket       = excluded.odds_bucket,
                confidence_bucket = excluded.confidence_bucket,
                edge_bucket       = excluded.edge_bucket,
                clv_bucket        = excluded.clv_bucket,
                sample_size       = excluded.sample_size,
                wins              = excluded.wins,
                losses            = excluded.losses,
                voids             = excluded.voids,
                hit_rate          = excluded.hit_rate,
                roi               = excluded.roi,
                avg_profit        = excluded.avg_profit,
                avg_clv_percent   = excluded.avg_clv_percent,
                clv_beat_rate     = excluded.clv_beat_rate,
                avg_edge          = excluded.avg_edge,
                avg_quality_score = excluded.avg_quality_score,
                avg_confidence    = excluded.avg_confidence,
                stability_score   = excluded.stability_score,
                strategy_score    = excluded.strategy_score,
                recommendation    = excluded.recommendation,
                metadata_json     = excluded.metadata_json
            """,
            [
                row.get("strategy_key"), row.get("market_key"),
                row.get("league_id"), row.get("league_name"), row.get("scope", "global"),
                row.get("odds_bucket"), row.get("confidence_bucket"),
                row.get("edge_bucket"), row.get("clv_bucket"),
                row.get("sample_size", 0), row.get("wins", 0),
                row.get("losses", 0), row.get("voids", 0),
                row.get("hit_rate"), row.get("roi"), row.get("avg_profit"),
                row.get("avg_clv_percent"), row.get("clv_beat_rate"),
                row.get("avg_edge"), row.get("avg_quality_score"),
                row.get("avg_confidence"), row.get("stability_score"),
                row.get("strategy_score"), row.get("recommendation", "insufficient_sample"),
                json.dumps(row.get("metadata")) if row.get("metadata") else None,
            ],
        )
        return True
    except Exception as exc:
        logger.warning("upsert_strategy_profile failed: %s", exc)
        return False


def upsert_strategy_learning_run(conn: "duckdb.DuckDBPyConnection", row: dict) -> bool:
    """Log a strategy learning run. Returns True on success."""
    try:
        conn.execute(
            """
            INSERT INTO strategy_learning_runs (
                id, created_at,
                run_key, days, total_picks,
                profiles_created, profiles_updated,
                best_strategy_key, worst_strategy_key, notes_json
            ) VALUES (
                nextval('strategy_learning_runs_seq'),
                now(),
                ?, ?, ?,
                ?, ?,
                ?, ?, ?
            )
            ON CONFLICT (run_key) DO UPDATE SET
                total_picks       = excluded.total_picks,
                profiles_created  = excluded.profiles_created,
                profiles_updated  = excluded.profiles_updated,
                best_strategy_key = excluded.best_strategy_key,
                worst_strategy_key= excluded.worst_strategy_key,
                notes_json        = excluded.notes_json
            """,
            [
                row.get("run_key"), row.get("days"), row.get("total_picks", 0),
                row.get("profiles_created", 0), row.get("profiles_updated", 0),
                row.get("best_strategy_key"), row.get("worst_strategy_key"),
                json.dumps(row.get("notes")) if row.get("notes") else None,
            ],
        )
        return True
    except Exception as exc:
        logger.warning("upsert_strategy_learning_run failed: %s", exc)
        return False


def upsert_strategy_adjustment(conn: "duckdb.DuckDBPyConnection", row: dict) -> bool:
    """Insert or update a strategy adjustment recommendation. Returns True on success."""
    try:
        conn.execute(
            """
            INSERT INTO strategy_adjustments (
                id, created_at,
                strategy_key, market_key, league_id,
                adjustment_type, adjustment_value,
                reason, evidence_sample_size, evidence_roi, evidence_clv,
                status, applied_at, metadata_json
            ) VALUES (
                nextval('strategy_adjustments_seq'),
                now(),
                ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?
            )
            ON CONFLICT (strategy_key, adjustment_type) DO UPDATE SET
                market_key           = excluded.market_key,
                league_id            = excluded.league_id,
                adjustment_value     = excluded.adjustment_value,
                reason               = excluded.reason,
                evidence_sample_size = excluded.evidence_sample_size,
                evidence_roi         = excluded.evidence_roi,
                evidence_clv         = excluded.evidence_clv,
                metadata_json        = excluded.metadata_json
            """,
            [
                row.get("strategy_key"), row.get("market_key"), row.get("league_id"),
                row.get("adjustment_type"), row.get("adjustment_value"),
                row.get("reason"), row.get("evidence_sample_size"),
                row.get("evidence_roi"), row.get("evidence_clv"),
                row.get("status", "pending"),
                row.get("applied_at"),
                json.dumps(row.get("metadata")) if row.get("metadata") else None,
            ],
        )
        return True
    except Exception as exc:
        logger.warning("upsert_strategy_adjustment failed: %s", exc)
        return False


def upsert_pick_learning_annotation(conn: "duckdb.DuckDBPyConnection", row: dict) -> bool:
    """Insert or update a pick learning annotation. Returns True on success."""
    try:
        conn.execute(
            """
            INSERT INTO pick_learning_annotations (
                id, created_at,
                pick_candidate_id, published_pick_id, fixture_id,
                strategy_key, market_key, league_id,
                pick_odds, pick_edge, pick_confidence,
                odds_bucket, confidence_bucket, edge_bucket, clv_bucket,
                result_status, profit, clv_percent, beat_closing_line,
                learning_label, metadata_json
            ) VALUES (
                nextval('pick_learning_annotations_seq'),
                now(),
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?
            )
            ON CONFLICT (pick_candidate_id) DO UPDATE SET
                published_pick_id = excluded.published_pick_id,
                fixture_id        = excluded.fixture_id,
                strategy_key      = excluded.strategy_key,
                market_key        = excluded.market_key,
                league_id         = excluded.league_id,
                pick_odds         = excluded.pick_odds,
                pick_edge         = excluded.pick_edge,
                pick_confidence   = excluded.pick_confidence,
                odds_bucket       = excluded.odds_bucket,
                confidence_bucket = excluded.confidence_bucket,
                edge_bucket       = excluded.edge_bucket,
                clv_bucket        = excluded.clv_bucket,
                result_status     = excluded.result_status,
                profit            = excluded.profit,
                clv_percent       = excluded.clv_percent,
                beat_closing_line = excluded.beat_closing_line,
                learning_label    = excluded.learning_label,
                metadata_json     = excluded.metadata_json
            """,
            [
                row.get("pick_candidate_id"), row.get("published_pick_id"),
                row.get("fixture_id"),
                row.get("strategy_key"), row.get("market_key"), row.get("league_id"),
                row.get("pick_odds"), row.get("pick_edge"), row.get("pick_confidence"),
                row.get("odds_bucket"), row.get("confidence_bucket"),
                row.get("edge_bucket"), row.get("clv_bucket"),
                row.get("result_status"), row.get("profit"),
                row.get("clv_percent"), row.get("beat_closing_line", False),
                row.get("learning_label", "no_data"),
                json.dumps(row.get("metadata")) if row.get("metadata") else None,
            ],
        )
        return True
    except Exception as exc:
        logger.warning("upsert_pick_learning_annotation failed: %s", exc)
        return False


# ── Reads ─────────────────────────────────────────────────────────────────────

def get_strategy_profiles(
    conn: "duckdb.DuckDBPyConnection",
    days: int | None = None,
    market_key: str | None = None,
    league_id: int | None = None,
) -> list[dict]:
    """Return strategy profiles, optionally filtered."""
    try:
        clauses = []
        params: list = []
        if days:
            clauses.append("updated_at >= current_timestamp - INTERVAL (?) DAY")
            params.append(days)
        if market_key:
            clauses.append("market_key = ?")
            params.append(market_key)
        if league_id is not None:
            clauses.append("league_id = ?")
            params.append(league_id)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"""
            SELECT strategy_key, market_key, league_id, league_name, scope,
                   odds_bucket, confidence_bucket, edge_bucket, clv_bucket,
                   sample_size, wins, losses, voids,
                   hit_rate, roi, avg_profit, avg_clv_percent, clv_beat_rate,
                   avg_edge, avg_quality_score, avg_confidence,
                   stability_score, strategy_score, recommendation, updated_at
            FROM strategy_profiles
            {where}
            ORDER BY strategy_score DESC NULLS LAST
            """,
            params,
        ).fetchall()
        cols = [
            "strategy_key", "market_key", "league_id", "league_name", "scope",
            "odds_bucket", "confidence_bucket", "edge_bucket", "clv_bucket",
            "sample_size", "wins", "losses", "voids",
            "hit_rate", "roi", "avg_profit", "avg_clv_percent", "clv_beat_rate",
            "avg_edge", "avg_quality_score", "avg_confidence",
            "stability_score", "strategy_score", "recommendation", "updated_at",
        ]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as exc:
        logger.warning("get_strategy_profiles failed: %s", exc)
        return []


def get_best_strategies(conn: "duckdb.DuckDBPyConnection", limit: int = 10) -> list[dict]:
    """Return top strategies by strategy_score with sufficient sample."""
    try:
        rows = conn.execute(
            """
            SELECT strategy_key, market_key, league_id, league_name,
                   sample_size, hit_rate, roi, avg_clv_percent, clv_beat_rate,
                   strategy_score, recommendation
            FROM strategy_profiles
            WHERE sample_size >= 10
              AND recommendation NOT IN ('insufficient_sample', 'avoid')
            ORDER BY strategy_score DESC NULLS LAST
            LIMIT ?
            """,
            [limit],
        ).fetchall()
        cols = [
            "strategy_key", "market_key", "league_id", "league_name",
            "sample_size", "hit_rate", "roi", "avg_clv_percent", "clv_beat_rate",
            "strategy_score", "recommendation",
        ]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as exc:
        logger.warning("get_best_strategies failed: %s", exc)
        return []


def get_weak_strategies(conn: "duckdb.DuckDBPyConnection", limit: int = 10) -> list[dict]:
    """Return lowest-scoring strategies with sufficient sample to be meaningful."""
    try:
        rows = conn.execute(
            """
            SELECT strategy_key, market_key, league_id, league_name,
                   sample_size, hit_rate, roi, avg_clv_percent, clv_beat_rate,
                   strategy_score, recommendation
            FROM strategy_profiles
            WHERE sample_size >= 10
            ORDER BY strategy_score ASC NULLS LAST
            LIMIT ?
            """,
            [limit],
        ).fetchall()
        cols = [
            "strategy_key", "market_key", "league_id", "league_name",
            "sample_size", "hit_rate", "roi", "avg_clv_percent", "clv_beat_rate",
            "strategy_score", "recommendation",
        ]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as exc:
        logger.warning("get_weak_strategies failed: %s", exc)
        return []


def get_strategy_by_key(conn: "duckdb.DuckDBPyConnection", strategy_key: str) -> dict | None:
    """Return a single strategy profile by key, or None."""
    try:
        row = conn.execute(
            """
            SELECT strategy_key, market_key, league_id, league_name, scope,
                   odds_bucket, confidence_bucket, edge_bucket, clv_bucket,
                   sample_size, wins, losses, voids,
                   hit_rate, roi, avg_profit, avg_clv_percent, clv_beat_rate,
                   avg_edge, avg_quality_score, avg_confidence,
                   stability_score, strategy_score, recommendation, updated_at
            FROM strategy_profiles
            WHERE strategy_key = ?
            """,
            [strategy_key],
        ).fetchone()
        if not row:
            return None
        cols = [
            "strategy_key", "market_key", "league_id", "league_name", "scope",
            "odds_bucket", "confidence_bucket", "edge_bucket", "clv_bucket",
            "sample_size", "wins", "losses", "voids",
            "hit_rate", "roi", "avg_profit", "avg_clv_percent", "clv_beat_rate",
            "avg_edge", "avg_quality_score", "avg_confidence",
            "stability_score", "strategy_score", "recommendation", "updated_at",
        ]
        return dict(zip(cols, row))
    except Exception as exc:
        logger.warning("get_strategy_by_key failed: %s", exc)
        return None


def get_strategy_adjustments(
    conn: "duckdb.DuckDBPyConnection",
    status: str | None = None,
) -> list[dict]:
    """Return strategy adjustments, optionally filtered by status."""
    try:
        where = "WHERE status = ?" if status else ""
        params = [status] if status else []
        rows = conn.execute(
            f"""
            SELECT strategy_key, market_key, league_id, adjustment_type,
                   adjustment_value, reason,
                   evidence_sample_size, evidence_roi, evidence_clv,
                   status, created_at
            FROM strategy_adjustments
            {where}
            ORDER BY created_at DESC
            """,
            params,
        ).fetchall()
        cols = [
            "strategy_key", "market_key", "league_id", "adjustment_type",
            "adjustment_value", "reason",
            "evidence_sample_size", "evidence_roi", "evidence_clv",
            "status", "created_at",
        ]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as exc:
        logger.warning("get_strategy_adjustments failed: %s", exc)
        return []


def get_learning_summary(conn: "duckdb.DuckDBPyConnection", days: int = 30) -> dict:
    """Return high-level stats about the learning pipeline."""
    try:
        total_profiles = conn.execute("SELECT COUNT(*) FROM strategy_profiles").fetchone()[0]
        total_annotations = conn.execute(
            "SELECT COUNT(*) FROM pick_learning_annotations"
        ).fetchone()[0]
        annotated_with_result = conn.execute(
            "SELECT COUNT(*) FROM pick_learning_annotations WHERE result_status IS NOT NULL"
        ).fetchone()[0]
        annotated_with_clv = conn.execute(
            "SELECT COUNT(*) FROM pick_learning_annotations WHERE clv_percent IS NOT NULL"
        ).fetchone()[0]

        rec_counts = {}
        rows = conn.execute(
            "SELECT recommendation, COUNT(*) FROM strategy_profiles GROUP BY recommendation"
        ).fetchall()
        for rec, cnt in rows:
            rec_counts[rec or "unknown"] = cnt

        best_row = conn.execute(
            """
            SELECT strategy_key, strategy_score
            FROM strategy_profiles
            WHERE strategy_score IS NOT NULL
            ORDER BY strategy_score DESC LIMIT 1
            """
        ).fetchone()
        worst_row = conn.execute(
            """
            SELECT strategy_key, strategy_score
            FROM strategy_profiles
            WHERE strategy_score IS NOT NULL AND sample_size >= 10
            ORDER BY strategy_score ASC LIMIT 1
            """
        ).fetchone()

        pending_adj = conn.execute(
            "SELECT COUNT(*) FROM strategy_adjustments WHERE status = 'pending'"
        ).fetchone()[0]

        return {
            "total_profiles":        total_profiles,
            "total_annotations":     total_annotations,
            "annotated_with_result": annotated_with_result,
            "annotated_with_clv":    annotated_with_clv,
            "recommendation_counts": rec_counts,
            "best_strategy_key":     best_row[0] if best_row else None,
            "best_strategy_score":   best_row[1] if best_row else None,
            "worst_strategy_key":    worst_row[0] if worst_row else None,
            "worst_strategy_score":  worst_row[1] if worst_row else None,
            "pending_adjustments":   pending_adj,
        }
    except Exception as exc:
        logger.warning("get_learning_summary failed: %s", exc)
        return {"error": str(exc)}


def audit_strategy_learning(conn: "duckdb.DuckDBPyConnection") -> dict:
    """Comprehensive audit of strategy learning tables."""
    try:
        tables = {}
        for t in ("strategy_profiles", "strategy_learning_runs",
                  "strategy_adjustments", "pick_learning_annotations"):
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                tables[t] = n
            except Exception:
                tables[t] = -1

        label_dist = {}
        try:
            rows = conn.execute(
                "SELECT learning_label, COUNT(*) FROM pick_learning_annotations GROUP BY learning_label"
            ).fetchall()
            label_dist = {r[0] or "null": r[1] for r in rows}
        except Exception:
            pass

        market_dist = {}
        try:
            rows = conn.execute(
                "SELECT market_key, COUNT(*) FROM strategy_profiles GROUP BY market_key"
            ).fetchall()
            market_dist = {r[0] or "null": r[1] for r in rows}
        except Exception:
            pass

        return {
            "table_counts":     tables,
            "label_dist":       label_dist,
            "market_dist":      market_dist,
        }
    except Exception as exc:
        return {"error": str(exc)}
