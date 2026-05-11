#!/usr/bin/env python
"""Phase 15: Audit model governance state — config, tables, experiments, results.

Usage:
    python scripts/audit_model_governance.py
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logger import setup_logger

logger = logging.getLogger(__name__)

EXPECTED_TABLES = [
    "experiment_registry",
    "experiment_pick_assignments",
    "experiment_results",
    "model_decision_audit",
    "activation_recommendations",
]


def _section(title: str) -> None:
    print(f"\n{'─' * 55}")
    print(f"  {title}")
    print('─' * 55)


def main() -> None:
    from app.core.config import settings
    from app.data.local.duckdb_client import get_local_db, init_schema
    from app.data.local.model_governance_repo import (
        get_active_experiments,
        get_activation_recommendations,
        get_governance_summary,
    )

    conn = get_local_db()
    init_schema(conn)

    print(f"\n{'=' * 55}")
    print("  Model Governance Audit — Phase 15")
    print(f"{'=' * 55}")

    _section("Config")
    print(f"  MODEL_GOVERNANCE_ENABLED             : {settings.model_governance_enabled}")
    print(f"  MODEL_GOVERNANCE_EXPERIMENTS_ENABLED : {settings.model_governance_experiments_enabled}")
    print(f"  MODEL_GOVERNANCE_DECISION_AUDIT_EN.  : {settings.model_governance_decision_audit_enabled}")
    print(f"  MODEL_GOVERNANCE_AUTO_ACTIVATE       : {settings.model_governance_auto_activate}")
    print(f"  MODEL_GOVERNANCE_WRITE_TO_DUCKDB     : {settings.model_governance_write_to_duckdb}")
    print(f"  MIN_SAMPLE strategy/bankroll/market/parlay: "
          f"{settings.model_governance_min_sample_strategy}/"
          f"{settings.model_governance_min_sample_bankroll}/"
          f"{settings.model_governance_min_sample_market}/"
          f"{settings.model_governance_min_sample_parlay}")
    print(f"  SCHEDULER_GOVERNANCE_ENABLED         : {settings.scheduler_governance_enabled}")
    print(f"  SCHEDULER_GOVERNANCE_TIME            : {settings.scheduler_governance_time}")

    _section("DB Tables")
    for tbl in EXPECTED_TABLES:
        try:
            r = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            print(f"  {tbl:40s} {r[0]:>6d} rows")
        except Exception as exc:
            print(f"  {tbl:40s} [MISSING] {exc}")

    _section("Active Experiments")
    try:
        experiments = get_active_experiments(conn)
        if experiments:
            for e in experiments:
                print(f"  [{e['status']:6s}] {e['experiment_key']:35s}  min={e['min_sample']}")
        else:
            print("  No active experiments — run register_experiments.py --execute")
    except Exception as exc:
        print(f"  [warn] {exc}")

    _section("Experiment Assignments (last 7d)")
    try:
        r = conn.execute(
            """
            SELECT experiment_key, COUNT(*),
                   SUM(CASE WHEN shadow_selected THEN 1 ELSE 0 END)
            FROM experiment_pick_assignments
            WHERE created_at >= current_timestamp - INTERVAL 7 DAY
            GROUP BY experiment_key
            ORDER BY experiment_key
            """
        ).fetchall()
        if r:
            for row in r:
                print(f"  {row[0]:35s}  total={row[1]}  shadow_selected={row[2]}")
        else:
            print("  No assignments in last 7 days")
    except Exception as exc:
        print(f"  [warn] {exc}")

    _section("Experiment Results (30d window)")
    try:
        r = conn.execute(
            """
            SELECT experiment_key, sample_size, roi, lift_vs_baseline,
                   gates_passed, gates_total, recommendation
            FROM experiment_results
            WHERE days_window = 30
            ORDER BY experiment_key
            """
        ).fetchall()
        if r:
            for row in r:
                roi_str  = f"{row[2] * 100:+.1f}%" if row[2] is not None else "n/a"
                lift_str = f"{row[3] * 100:+.1f}%" if row[3] is not None else "n/a"
                gates    = f"{row[4]}/{row[5]}" if row[5] else "n/a"
                print(f"  {row[0]:35s}  n={row[1]:4d}  roi={roi_str:7s}  "
                      f"lift={lift_str:7s}  gates={gates}  {row[6]}")
        else:
            print("  No results yet — run run_experiment_lab.py --execute")
    except Exception as exc:
        print(f"  [warn] {exc}")

    _section("Decision Audits (last 7d)")
    try:
        r = conn.execute(
            """
            SELECT COUNT(*),
                   SUM(CASE WHEN decision_changed THEN 1 ELSE 0 END),
                   SUM(CASE WHEN safety_blocked THEN 1 ELSE 0 END)
            FROM model_decision_audit
            WHERE created_at >= current_timestamp - INTERVAL 7 DAY
            """
        ).fetchone()
        print(f"  Total audits    : {r[0] or 0}")
        print(f"  Decision changed: {r[1] or 0}")
        print(f"  Safety blocked  : {r[2] or 0}")
    except Exception as exc:
        print(f"  [warn] {exc}")

    _section("Activation Recommendations")
    try:
        recs = get_activation_recommendations(conn)
        if recs:
            for rec in recs:
                gp = rec.get("gates_passed", 0)
                gt = rec.get("gates_total", 0)
                gates_str = f"{gp}/{gt}" if gt else "n/a"
                print(f"  {rec['module']:20s}  {rec['recommendation']:30s}  gates={gates_str}")
        else:
            print("  No recommendations yet — run run_experiment_lab.py --execute")
    except Exception as exc:
        print(f"  [warn] {exc}")

    _section("Blockers / Warnings")
    blockers = []
    try:
        recs = get_activation_recommendations(conn)
        for rec in recs:
            reason = rec.get("blocking_reason")
            if reason:
                blockers.append(f"  [{rec['module']}] {reason}")
    except Exception:
        pass
    if blockers:
        for b in blockers:
            print(b)
    else:
        print("  None detected")

    print(f"\n{'=' * 55}\n")


if __name__ == "__main__":
    setup_logger()
    main()
