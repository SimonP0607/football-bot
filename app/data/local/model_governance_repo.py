"""Phase 15: Model Governance — DuckDB repository functions."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

# ── Default experiment definitions ─────────────────────────────────────────────
DEFAULT_EXPERIMENTS = [
    {
        "experiment_key": "baseline_current",
        "variant_name": "Baseline — Current Production Pipeline",
        "module": "value_engine",
        "description": "Reference baseline: picks selected by the current production pipeline without any module overrides.",
        "min_sample": 50,
    },
    {
        "experiment_key": "variant_value_engine",
        "variant_name": "Variant — Enhanced Value Engine",
        "module": "value_engine",
        "description": "Shadow comparison using enhanced edge + quality thresholds from the value engine.",
        "min_sample": 100,
    },
    {
        "experiment_key": "variant_strategy_learning",
        "variant_name": "Variant — Strategy Learning Signals",
        "module": "strategy_learning",
        "description": "Shadow comparison applying strategy profile promote/reduce/avoid signals to pick selection.",
        "min_sample": 200,
    },
    {
        "experiment_key": "variant_bankroll_conservative",
        "variant_name": "Variant — Bankroll Conservative Sizing",
        "module": "bankroll",
        "description": "Shadow comparison with Kelly fractional stake sizing and portfolio risk controls.",
        "min_sample": 100,
    },
    {
        "experiment_key": "variant_market_clv",
        "variant_name": "Variant — Market CLV Filter",
        "module": "market_clv",
        "description": "Shadow comparison filtering picks by positive closing-line value and sharp move signals.",
        "min_sample": 150,
    },
    {
        "experiment_key": "variant_parlay_filtered",
        "variant_name": "Variant — Parlay Correlation Filter",
        "module": "parlay",
        "description": "Shadow comparison applying correlation controls and ROI gates to parlay candidates.",
        "min_sample": 50,
    },
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _next_id(conn, sequence: str) -> int:
    return conn.execute(f"SELECT nextval('{sequence}')").fetchone()[0]


def _row_to_dict(row, description) -> dict[str, Any]:
    return {col[0]: row[i] for i, col in enumerate(description)}


def _rows_to_dicts(rows, description) -> list[dict[str, Any]]:
    return [_row_to_dict(r, description) for r in rows]


# ── experiment_registry ────────────────────────────────────────────────────────

def create_experiment(
    conn,
    experiment_key: str,
    variant_name: str,
    module: str,
    description: str = "",
    status: str = "active",
    min_sample: int = 100,
    config_json: dict | None = None,
) -> dict[str, Any]:
    """Insert or update an experiment definition. Returns the row."""
    cfg = json.dumps(config_json) if config_json else None
    now = datetime.utcnow()

    existing = conn.execute(
        "SELECT id FROM experiment_registry WHERE experiment_key = ?",
        [experiment_key],
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE experiment_registry
            SET variant_name = ?, module = ?, description = ?, status = ?,
                min_sample = ?, config_json = ?, updated_at = ?
            WHERE experiment_key = ?
            """,
            [variant_name, module, description, status, min_sample, cfg, now, experiment_key],
        )
    else:
        eid = _next_id(conn, "experiment_registry_seq")
        conn.execute(
            """
            INSERT INTO experiment_registry
                (id, experiment_key, variant_name, module, description,
                 status, min_sample, config_json, started_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [eid, experiment_key, variant_name, module, description,
             status, min_sample, cfg, now, now, now],
        )

    row = conn.execute(
        "SELECT * FROM experiment_registry WHERE experiment_key = ?",
        [experiment_key],
    ).fetchone()
    desc = conn.description
    return _row_to_dict(row, desc) if row else {}


def get_active_experiments(conn) -> list[dict[str, Any]]:
    """Return all active experiment definitions."""
    rows = conn.execute(
        "SELECT * FROM experiment_registry WHERE status = 'active' ORDER BY module, experiment_key"
    ).fetchall()
    return _rows_to_dicts(rows, conn.description)


def get_experiment(conn, experiment_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM experiment_registry WHERE experiment_key = ?",
        [experiment_key],
    ).fetchone()
    if not row:
        return None
    return _row_to_dict(row, conn.description)


# ── experiment_pick_assignments ────────────────────────────────────────────────

def upsert_experiment_assignment(
    conn,
    pick_candidate_id: int,
    experiment_key: str,
    fixture_id: int | None = None,
    market_key: str | None = None,
    league_id: int | None = None,
    assigned_date: date | None = None,
    shadow_edge: float | None = None,
    shadow_confidence: float | None = None,
    shadow_quality: float | None = None,
    shadow_selected: bool = False,
    metadata: dict | None = None,
) -> None:
    meta = json.dumps(metadata) if metadata else None
    adate = assigned_date or date.today()

    existing = conn.execute(
        """
        SELECT id FROM experiment_pick_assignments
        WHERE pick_candidate_id = ? AND experiment_key = ?
        """,
        [pick_candidate_id, experiment_key],
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE experiment_pick_assignments
            SET shadow_edge = ?, shadow_confidence = ?, shadow_quality = ?,
                shadow_selected = ?, metadata_json = ?
            WHERE pick_candidate_id = ? AND experiment_key = ?
            """,
            [shadow_edge, shadow_confidence, shadow_quality,
             shadow_selected, meta, pick_candidate_id, experiment_key],
        )
    else:
        aid = _next_id(conn, "experiment_pick_assignments_seq")
        conn.execute(
            """
            INSERT INTO experiment_pick_assignments
                (id, pick_candidate_id, fixture_id, experiment_key, market_key,
                 league_id, assigned_date, shadow_edge, shadow_confidence,
                 shadow_quality, shadow_selected, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [aid, pick_candidate_id, fixture_id, experiment_key, market_key,
             league_id, adate, shadow_edge, shadow_confidence,
             shadow_quality, shadow_selected, meta, datetime.utcnow()],
        )


def get_assignments_by_day(
    conn,
    assigned_date: date,
    experiment_key: str | None = None,
) -> list[dict[str, Any]]:
    if experiment_key:
        rows = conn.execute(
            """
            SELECT * FROM experiment_pick_assignments
            WHERE assigned_date = ? AND experiment_key = ?
            ORDER BY created_at
            """,
            [assigned_date, experiment_key],
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM experiment_pick_assignments WHERE assigned_date = ? ORDER BY created_at",
            [assigned_date],
        ).fetchall()
    return _rows_to_dicts(rows, conn.description)


# ── experiment_results ─────────────────────────────────────────────────────────

def upsert_experiment_result(
    conn,
    experiment_key: str,
    days_window: int = 30,
    *,
    sample_size: int = 0,
    wins: int = 0,
    losses: int = 0,
    voids: int = 0,
    hit_rate: float | None = None,
    roi: float | None = None,
    avg_edge: float | None = None,
    avg_clv_percent: float | None = None,
    clv_beat_rate: float | None = None,
    max_drawdown: float | None = None,
    confidence_interval_low: float | None = None,
    confidence_interval_high: float | None = None,
    lift_vs_baseline: float | None = None,
    baseline_roi: float | None = None,
    baseline_sample: int | None = None,
    gates_passed: int = 0,
    gates_total: int = 0,
    recommendation: str = "DO_NOT_ACTIVATE",
    notes: dict | None = None,
) -> None:
    notes_json = json.dumps(notes) if notes else None
    now = datetime.utcnow()

    existing = conn.execute(
        "SELECT id FROM experiment_results WHERE experiment_key = ? AND days_window = ?",
        [experiment_key, days_window],
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE experiment_results
            SET updated_at = ?, sample_size = ?, wins = ?, losses = ?, voids = ?,
                hit_rate = ?, roi = ?, avg_edge = ?, avg_clv_percent = ?,
                clv_beat_rate = ?, max_drawdown = ?,
                confidence_interval_low = ?, confidence_interval_high = ?,
                lift_vs_baseline = ?, baseline_roi = ?, baseline_sample = ?,
                gates_passed = ?, gates_total = ?, recommendation = ?, notes_json = ?
            WHERE experiment_key = ? AND days_window = ?
            """,
            [now, sample_size, wins, losses, voids,
             hit_rate, roi, avg_edge, avg_clv_percent,
             clv_beat_rate, max_drawdown,
             confidence_interval_low, confidence_interval_high,
             lift_vs_baseline, baseline_roi, baseline_sample,
             gates_passed, gates_total, recommendation, notes_json,
             experiment_key, days_window],
        )
    else:
        rid = _next_id(conn, "experiment_results_seq")
        conn.execute(
            """
            INSERT INTO experiment_results
                (id, created_at, updated_at, experiment_key, days_window,
                 sample_size, wins, losses, voids, hit_rate, roi, avg_edge,
                 avg_clv_percent, clv_beat_rate, max_drawdown,
                 confidence_interval_low, confidence_interval_high,
                 lift_vs_baseline, baseline_roi, baseline_sample,
                 gates_passed, gates_total, recommendation, notes_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [rid, now, now, experiment_key, days_window,
             sample_size, wins, losses, voids, hit_rate, roi, avg_edge,
             avg_clv_percent, clv_beat_rate, max_drawdown,
             confidence_interval_low, confidence_interval_high,
             lift_vs_baseline, baseline_roi, baseline_sample,
             gates_passed, gates_total, recommendation, notes_json],
        )


def get_experiment_results(
    conn,
    experiment_key: str | None = None,
    days_window: int = 30,
) -> list[dict[str, Any]]:
    if experiment_key:
        rows = conn.execute(
            "SELECT * FROM experiment_results WHERE experiment_key = ? AND days_window = ?",
            [experiment_key, days_window],
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM experiment_results WHERE days_window = ? ORDER BY experiment_key",
            [days_window],
        ).fetchall()
    return _rows_to_dicts(rows, conn.description)


# ── model_decision_audit ───────────────────────────────────────────────────────

def insert_model_decision_audit(
    conn,
    pick_candidate_id: int | None = None,
    published_pick_id: int | None = None,
    fixture_id: int | None = None,
    market_key: str | None = None,
    league_id: int | None = None,
    assigned_date: date | None = None,
    official_selected: bool | None = None,
    official_edge: float | None = None,
    official_quality: float | None = None,
    ve_selected: bool | None = None,
    ve_edge: float | None = None,
    ve_quality: float | None = None,
    sl_recommendation: str | None = None,
    sl_strategy_score: float | None = None,
    br_recommended_units: float | None = None,
    br_risk_label: str | None = None,
    br_rejected: bool | None = None,
    mi_signal: str | None = None,
    mi_clv_percent: float | None = None,
    final_selected: bool | None = None,
    decision_changed: bool = False,
    change_reason: str | None = None,
    safety_blocked: bool = False,
    metadata: dict | None = None,
) -> None:
    meta = json.dumps(metadata) if metadata else None
    adate = assigned_date or date.today()

    existing = None
    if pick_candidate_id is not None:
        existing = conn.execute(
            "SELECT id FROM model_decision_audit WHERE pick_candidate_id = ?",
            [pick_candidate_id],
        ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE model_decision_audit
            SET official_selected = ?, official_edge = ?, official_quality = ?,
                ve_selected = ?, ve_edge = ?, ve_quality = ?,
                sl_recommendation = ?, sl_strategy_score = ?,
                br_recommended_units = ?, br_risk_label = ?, br_rejected = ?,
                mi_signal = ?, mi_clv_percent = ?,
                final_selected = ?, decision_changed = ?, change_reason = ?,
                safety_blocked = ?, metadata_json = ?
            WHERE pick_candidate_id = ?
            """,
            [official_selected, official_edge, official_quality,
             ve_selected, ve_edge, ve_quality,
             sl_recommendation, sl_strategy_score,
             br_recommended_units, br_risk_label, br_rejected,
             mi_signal, mi_clv_percent,
             final_selected, decision_changed, change_reason,
             safety_blocked, meta, pick_candidate_id],
        )
    else:
        aid = _next_id(conn, "model_decision_audit_seq")
        conn.execute(
            """
            INSERT INTO model_decision_audit
                (id, created_at, pick_candidate_id, published_pick_id, fixture_id,
                 market_key, league_id, assigned_date,
                 official_selected, official_edge, official_quality,
                 ve_selected, ve_edge, ve_quality,
                 sl_recommendation, sl_strategy_score,
                 br_recommended_units, br_risk_label, br_rejected,
                 mi_signal, mi_clv_percent,
                 final_selected, decision_changed, change_reason,
                 safety_blocked, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [aid, datetime.utcnow(), pick_candidate_id, published_pick_id, fixture_id,
             market_key, league_id, adate,
             official_selected, official_edge, official_quality,
             ve_selected, ve_edge, ve_quality,
             sl_recommendation, sl_strategy_score,
             br_recommended_units, br_risk_label, br_rejected,
             mi_signal, mi_clv_percent,
             final_selected, decision_changed, change_reason,
             safety_blocked, meta],
        )


def get_model_decision_audit(
    conn,
    days: int = 7,
    limit: int = 100,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM model_decision_audit
        WHERE created_at >= current_timestamp - INTERVAL (?) DAY
        ORDER BY created_at DESC
        LIMIT ?
        """,
        [days, limit],
    ).fetchall()
    return _rows_to_dicts(rows, conn.description)


# ── activation_recommendations ─────────────────────────────────────────────────

def upsert_activation_recommendation(
    conn,
    module: str,
    recommendation: str,
    sample_size: int = 0,
    gates_passed: int = 0,
    gates_total: int = 0,
    gate_details: dict | None = None,
    roi: float | None = None,
    lift_vs_baseline: float | None = None,
    drawdown: float | None = None,
    clv_avg: float | None = None,
    blocking_reason: str | None = None,
    notes: str | None = None,
    valid_hours: int = 24,
) -> None:
    gate_json = json.dumps(gate_details) if gate_details else None
    now = datetime.utcnow()
    from datetime import timedelta
    valid_until = now + timedelta(hours=valid_hours)

    existing = conn.execute(
        "SELECT id FROM activation_recommendations WHERE module = ?",
        [module],
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE activation_recommendations
            SET updated_at = ?, recommendation = ?, sample_size = ?,
                gates_passed = ?, gates_total = ?, gate_details_json = ?,
                roi = ?, lift_vs_baseline = ?, drawdown = ?, clv_avg = ?,
                blocking_reason = ?, notes = ?, valid_until = ?
            WHERE module = ?
            """,
            [now, recommendation, sample_size,
             gates_passed, gates_total, gate_json,
             roi, lift_vs_baseline, drawdown, clv_avg,
             blocking_reason, notes, valid_until, module],
        )
    else:
        rid = _next_id(conn, "activation_recommendations_seq")
        conn.execute(
            """
            INSERT INTO activation_recommendations
                (id, created_at, updated_at, module, recommendation, sample_size,
                 gates_passed, gates_total, gate_details_json,
                 roi, lift_vs_baseline, drawdown, clv_avg,
                 blocking_reason, notes, valid_until)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [rid, now, now, module, recommendation, sample_size,
             gates_passed, gates_total, gate_json,
             roi, lift_vs_baseline, drawdown, clv_avg,
             blocking_reason, notes, valid_until],
        )


def get_activation_recommendations(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM activation_recommendations ORDER BY module"
    ).fetchall()
    return _rows_to_dicts(rows, conn.description)


def get_activation_recommendation(conn, module: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM activation_recommendations WHERE module = ?",
        [module],
    ).fetchone()
    if not row:
        return None
    return _row_to_dict(row, conn.description)


# ── Summary ────────────────────────────────────────────────────────────────────

def get_governance_summary(conn) -> dict[str, Any]:
    """High-level governance dashboard data."""
    summary: dict[str, Any] = {}

    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM experiment_registry GROUP BY status"
        ).fetchall()
        summary["experiments"] = {r[0]: r[1] for r in rows}
    except Exception as exc:
        summary["experiments"] = {"error": str(exc)}

    try:
        r = conn.execute(
            "SELECT COUNT(*) FROM model_decision_audit WHERE created_at >= current_timestamp - INTERVAL 7 DAY"
        ).fetchone()
        changed = conn.execute(
            "SELECT COUNT(*) FROM model_decision_audit WHERE decision_changed = true AND created_at >= current_timestamp - INTERVAL 7 DAY"
        ).fetchone()
        summary["audit_7d"] = {
            "total": r[0] or 0,
            "changed": changed[0] or 0,
        }
    except Exception as exc:
        summary["audit_7d"] = {"error": str(exc)}

    try:
        rows = conn.execute(
            "SELECT module, recommendation FROM activation_recommendations ORDER BY module"
        ).fetchall()
        summary["recommendations"] = {r[0]: r[1] for r in rows}
    except Exception as exc:
        summary["recommendations"] = {"error": str(exc)}

    return summary
