#!/usr/bin/env python
"""Phase 15: Test suite for Model Governance, Experiment Lab & Safe Activation.

Usage:
    python scripts/test_model_governance.py
"""
from __future__ import annotations

import importlib
import inspect
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Test harness ───────────────────────────────────────────────────────────────
_passed = 0
_failed = 0
_group_name = ""


def group(name: str) -> None:
    global _group_name
    _group_name = name
    print(f"\n[{name}]")


def ok(label: str) -> None:
    global _passed
    _passed += 1
    print(f"  PASS  {label}")


def fail(label: str, reason: str = "") -> None:
    global _failed
    _failed += 1
    msg = f"  FAIL  {label}"
    if reason:
        msg += f" — {reason}"
    print(msg)


def assert_eq(label: str, got, expected) -> None:
    if got == expected:
        ok(label)
    else:
        fail(label, f"got={got!r} expected={expected!r}")


def assert_approx(label: str, got, expected, tol: float = 0.001) -> None:
    if got is None and expected is None:
        ok(label)
        return
    try:
        if abs(got - expected) <= tol:
            ok(label)
        else:
            fail(label, f"got={got!r} expected={expected!r} tol={tol}")
    except (TypeError, ValueError) as e:
        fail(label, str(e))


def assert_true(label: str, value) -> None:
    if value:
        ok(label)
    else:
        fail(label, f"got={value!r}")


def assert_false(label: str, value) -> None:
    if not value:
        ok(label)
    else:
        fail(label, f"expected falsy, got={value!r}")


def assert_in(label: str, item, container) -> None:
    if item in container:
        ok(label)
    else:
        snippet = repr(container)[:120] if isinstance(container, str) else repr(container)
        fail(label, f"{item!r} not in container (excerpt: {snippet})")


def assert_gte(label: str, got, threshold) -> None:
    if got >= threshold:
        ok(label)
    else:
        fail(label, f"got={got!r} < {threshold!r}")


def assert_lte(label: str, got, threshold) -> None:
    if got <= threshold:
        ok(label)
    else:
        fail(label, f"got={got!r} > {threshold!r}")


# ── In-memory DuckDB fixture ───────────────────────────────────────────────────

def _make_conn():
    import duckdb
    conn = duckdb.connect(":memory:")
    # Load schema
    sql_path = Path(__file__).parent.parent / "sql" / "local" / "015_model_governance_schema.sql"
    conn.execute(sql_path.read_text(encoding="utf-8"))
    # Also load Phase 13 schema (pick_learning_annotations for join tests)
    sql13 = Path(__file__).parent.parent / "sql" / "local" / "013_strategy_learning_schema.sql"
    if sql13.exists():
        conn.execute(sql13.read_text())
    return conn


# ── Group 1: SQL schema idempotency ───────────────────────────────────────────
group("1 — SQL schema idempotency")
try:
    conn = _make_conn()
    # Run again — must not fail
    sql_path = Path(__file__).parent.parent / "sql" / "local" / "015_model_governance_schema.sql"
    conn.execute(sql_path.read_text(encoding="utf-8"))
    ok("schema runs twice without error")
    for tbl in ["experiment_registry", "experiment_pick_assignments",
                "experiment_results", "model_decision_audit",
                "activation_recommendations"]:
        r = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
        assert_eq(f"table {tbl} exists and is empty", r[0], 0)
    conn.close()
except Exception as e:
    fail("schema idempotency", str(e))


# ── Group 2: experiment_registry sequences ─────────────────────────────────────
group("2 — experiment_registry sequences")
try:
    conn = _make_conn()
    r1 = conn.execute("SELECT nextval('experiment_registry_seq')").fetchone()[0]
    r2 = conn.execute("SELECT nextval('experiment_registry_seq')").fetchone()[0]
    assert_eq("seq increments", r2, r1 + 1)
    for seq in ["experiment_pick_assignments_seq", "experiment_results_seq",
                "model_decision_audit_seq", "activation_recommendations_seq"]:
        v = conn.execute(f"SELECT nextval('{seq}')").fetchone()[0]
        assert_true(f"seq {seq} valid", isinstance(v, int) and v >= 1)
    conn.close()
except Exception as e:
    fail("sequences", str(e))


# ── Group 3: experiment_registry unique index ──────────────────────────────────
group("3 — experiment_registry unique index")
try:
    from app.data.local.model_governance_repo import create_experiment
    conn = _make_conn()
    create_experiment(conn, "test_exp_1", "Test", "value_engine")
    # Second call updates, not errors
    row = create_experiment(conn, "test_exp_1", "Test Updated", "value_engine")
    assert_eq("variant_name updated", row["variant_name"], "Test Updated")
    r = conn.execute("SELECT COUNT(*) FROM experiment_registry WHERE experiment_key='test_exp_1'").fetchone()
    assert_eq("only one row", r[0], 1)
    conn.close()
except Exception as e:
    fail("unique index upsert", str(e))


# ── Group 4: create_experiment returns dict ────────────────────────────────────
group("4 — create_experiment returns dict")
try:
    from app.data.local.model_governance_repo import create_experiment
    conn = _make_conn()
    row = create_experiment(
        conn, "baseline_current", "Baseline", "value_engine",
        description="baseline desc", min_sample=50
    )
    assert_eq("experiment_key", row["experiment_key"], "baseline_current")
    assert_eq("module", row["module"], "value_engine")
    assert_eq("min_sample", row["min_sample"], 50)
    assert_eq("status default active", row["status"], "active")
    conn.close()
except Exception as e:
    fail("create_experiment dict", str(e))


# ── Group 5: get_active_experiments ───────────────────────────────────────────
group("5 — get_active_experiments")
try:
    from app.data.local.model_governance_repo import create_experiment, get_active_experiments
    conn = _make_conn()
    create_experiment(conn, "exp_active_1", "A1", "value_engine", status="active")
    create_experiment(conn, "exp_active_2", "A2", "strategy_learning", status="active")
    create_experiment(conn, "exp_paused_1", "P1", "bankroll", status="paused")
    active = get_active_experiments(conn)
    keys = [e["experiment_key"] for e in active]
    assert_in("active 1 present", "exp_active_1", keys)
    assert_in("active 2 present", "exp_active_2", keys)
    assert_false("paused not in active", "exp_paused_1" in keys)
    conn.close()
except Exception as e:
    fail("get_active_experiments", str(e))


# ── Group 6: get_experiment ────────────────────────────────────────────────────
group("6 — get_experiment by key")
try:
    from app.data.local.model_governance_repo import create_experiment, get_experiment
    conn = _make_conn()
    create_experiment(conn, "find_me", "Find Me", "parlay")
    row = get_experiment(conn, "find_me")
    assert_eq("key correct", row["experiment_key"], "find_me")
    none_row = get_experiment(conn, "nonexistent")
    assert_eq("nonexistent returns None", none_row, None)
    conn.close()
except Exception as e:
    fail("get_experiment", str(e))


# ── Group 7: upsert_experiment_assignment ─────────────────────────────────────
group("7 — upsert_experiment_assignment insert")
try:
    from app.data.local.model_governance_repo import upsert_experiment_assignment
    conn = _make_conn()
    upsert_experiment_assignment(
        conn, pick_candidate_id=1001, experiment_key="baseline_current",
        fixture_id=500, market_key="1X2", league_id=10,
        shadow_edge=0.05, shadow_confidence=0.72, shadow_selected=True
    )
    r = conn.execute(
        "SELECT * FROM experiment_pick_assignments WHERE pick_candidate_id=1001 AND experiment_key='baseline_current'"
    ).fetchone()
    assert_true("row inserted", r is not None)
    desc = conn.description
    row = {col[0]: r[i] for i, col in enumerate(desc)}
    assert_eq("market_key", row["market_key"], "1X2")
    assert_true("shadow_selected", row["shadow_selected"])
    conn.close()
except Exception as e:
    fail("upsert_assignment insert", str(e))


# ── Group 8: upsert_experiment_assignment update ──────────────────────────────
group("8 — upsert_experiment_assignment update")
try:
    from app.data.local.model_governance_repo import upsert_experiment_assignment
    conn = _make_conn()
    upsert_experiment_assignment(conn, 2001, "variant_strategy_learning", shadow_edge=0.03)
    upsert_experiment_assignment(conn, 2001, "variant_strategy_learning", shadow_edge=0.08, shadow_selected=True)
    r = conn.execute(
        "SELECT COUNT(*) FROM experiment_pick_assignments WHERE pick_candidate_id=2001"
    ).fetchone()
    assert_eq("only one row after update", r[0], 1)
    v = conn.execute(
        "SELECT shadow_edge FROM experiment_pick_assignments WHERE pick_candidate_id=2001"
    ).fetchone()[0]
    assert_approx("shadow_edge updated", v, 0.08)
    conn.close()
except Exception as e:
    fail("upsert_assignment update", str(e))


# ── Group 9: get_assignments_by_day ───────────────────────────────────────────
group("9 — get_assignments_by_day")
try:
    from app.data.local.model_governance_repo import upsert_experiment_assignment, get_assignments_by_day
    conn = _make_conn()
    today = date.today()
    yesterday = today - timedelta(days=1)
    upsert_experiment_assignment(conn, 3001, "baseline_current", assigned_date=today)
    upsert_experiment_assignment(conn, 3002, "baseline_current", assigned_date=today)
    upsert_experiment_assignment(conn, 3003, "variant_market_clv", assigned_date=yesterday)
    rows_today = get_assignments_by_day(conn, today)
    assert_eq("two rows today", len(rows_today), 2)
    rows_key = get_assignments_by_day(conn, yesterday, experiment_key="variant_market_clv")
    assert_eq("one row yesterday filtered", len(rows_key), 1)
    conn.close()
except Exception as e:
    fail("get_assignments_by_day", str(e))


# ── Group 10: upsert_experiment_result insert ─────────────────────────────────
group("10 — upsert_experiment_result insert")
try:
    from app.data.local.model_governance_repo import upsert_experiment_result, get_experiment_results
    conn = _make_conn()
    upsert_experiment_result(
        conn, "variant_bankroll_conservative", days_window=30,
        sample_size=120, wins=60, losses=50, voids=10,
        hit_rate=0.545, roi=0.04, avg_edge=0.06,
        avg_clv_percent=0.02, clv_beat_rate=0.55,
        max_drawdown=2.1, confidence_interval_low=0.45, confidence_interval_high=0.64,
        lift_vs_baseline=0.02, baseline_roi=0.02, baseline_sample=200,
        gates_passed=5, gates_total=6, recommendation="SAFE_TO_TEST_SHADOW"
    )
    results = get_experiment_results(conn, "variant_bankroll_conservative", 30)
    assert_eq("one result", len(results), 1)
    r = results[0]
    assert_eq("sample_size", r["sample_size"], 120)
    assert_eq("recommendation", r["recommendation"], "SAFE_TO_TEST_SHADOW")
    assert_approx("roi", r["roi"], 0.04)
    conn.close()
except Exception as e:
    fail("upsert_result insert", str(e))


# ── Group 11: upsert_experiment_result update ─────────────────────────────────
group("11 — upsert_experiment_result update")
try:
    from app.data.local.model_governance_repo import upsert_experiment_result, get_experiment_results
    conn = _make_conn()
    upsert_experiment_result(conn, "variant_parlay_filtered", days_window=30,
                              sample_size=40, recommendation="DO_NOT_ACTIVATE")
    upsert_experiment_result(conn, "variant_parlay_filtered", days_window=30,
                              sample_size=55, recommendation="OBSERVE_MORE")
    results = get_experiment_results(conn, "variant_parlay_filtered", 30)
    assert_eq("only one row", len(results), 1)
    assert_eq("updated sample", results[0]["sample_size"], 55)
    assert_eq("updated rec", results[0]["recommendation"], "OBSERVE_MORE")
    conn.close()
except Exception as e:
    fail("upsert_result update", str(e))


# ── Group 12: get_experiment_results all ──────────────────────────────────────
group("12 — get_experiment_results all")
try:
    from app.data.local.model_governance_repo import upsert_experiment_result, get_experiment_results
    conn = _make_conn()
    for key in ["baseline_current", "variant_value_engine", "variant_strategy_learning"]:
        upsert_experiment_result(conn, key, days_window=30, sample_size=100)
    results = get_experiment_results(conn, days_window=30)
    assert_eq("three results", len(results), 3)
    conn.close()
except Exception as e:
    fail("get_experiment_results all", str(e))


# ── Group 13: insert_model_decision_audit ─────────────────────────────────────
group("13 — insert_model_decision_audit insert")
try:
    from app.data.local.model_governance_repo import insert_model_decision_audit, get_model_decision_audit
    conn = _make_conn()
    insert_model_decision_audit(
        conn, pick_candidate_id=5001, fixture_id=100, market_key="1X2",
        league_id=10, official_selected=True, official_edge=0.07,
        official_quality=75.0, ve_selected=True, ve_edge=0.07,
        sl_recommendation="promote", sl_strategy_score=80.0,
        br_recommended_units=0.8, br_risk_label="low", br_rejected=False,
        mi_signal="steam_move", mi_clv_percent=2.3,
        final_selected=True, decision_changed=False, safety_blocked=False
    )
    audits = get_model_decision_audit(conn, days=7)
    assert_eq("one audit", len(audits), 1)
    a = audits[0]
    assert_eq("pick_candidate_id", a["pick_candidate_id"], 5001)
    assert_eq("sl_recommendation", a["sl_recommendation"], "promote")
    assert_approx("br_recommended_units", a["br_recommended_units"], 0.8)
    conn.close()
except Exception as e:
    fail("insert_decision_audit", str(e))


# ── Group 14: insert_model_decision_audit update (upsert) ─────────────────────
group("14 — insert_model_decision_audit update")
try:
    from app.data.local.model_governance_repo import insert_model_decision_audit, get_model_decision_audit
    conn = _make_conn()
    insert_model_decision_audit(conn, pick_candidate_id=6001, official_selected=True)
    insert_model_decision_audit(conn, pick_candidate_id=6001, official_selected=False,
                                decision_changed=True, change_reason="strategy_avoid")
    audits = get_model_decision_audit(conn, days=7)
    assert_eq("one row after update", len(audits), 1)
    assert_eq("decision_changed", audits[0]["decision_changed"], True)
    assert_eq("change_reason", audits[0]["change_reason"], "strategy_avoid")
    conn.close()
except Exception as e:
    fail("decision_audit upsert", str(e))


# ── Group 15: upsert_activation_recommendation ────────────────────────────────
group("15 — upsert_activation_recommendation insert")
try:
    from app.data.local.model_governance_repo import (
        upsert_activation_recommendation, get_activation_recommendation
    )
    conn = _make_conn()
    upsert_activation_recommendation(
        conn, module="strategy_learning",
        recommendation="SAFE_TO_TEST_SHADOW",
        sample_size=220, gates_passed=5, gates_total=6,
        gate_details={"min_sample_200": True, "roi_beats_baseline_3pct": True},
        roi=0.065, lift_vs_baseline=0.025, drawdown=3.1, clv_avg=0.012
    )
    rec = get_activation_recommendation(conn, "strategy_learning")
    assert_eq("recommendation", rec["recommendation"], "SAFE_TO_TEST_SHADOW")
    assert_eq("sample_size", rec["sample_size"], 220)
    assert_eq("gates_passed", rec["gates_passed"], 5)
    conn.close()
except Exception as e:
    fail("upsert_activation_rec", str(e))


# ── Group 16: upsert_activation_recommendation update ─────────────────────────
group("16 — upsert_activation_recommendation update")
try:
    from app.data.local.model_governance_repo import (
        upsert_activation_recommendation, get_activation_recommendation
    )
    conn = _make_conn()
    upsert_activation_recommendation(conn, "bankroll", "DO_NOT_ACTIVATE", sample_size=50)
    upsert_activation_recommendation(conn, "bankroll", "OBSERVE_MORE", sample_size=90)
    rec = get_activation_recommendation(conn, "bankroll")
    r2 = conn.execute("SELECT COUNT(*) FROM activation_recommendations WHERE module='bankroll'").fetchone()
    assert_eq("only one row", r2[0], 1)
    assert_eq("recommendation updated", rec["recommendation"], "OBSERVE_MORE")
    conn.close()
except Exception as e:
    fail("upsert_activation_rec update", str(e))


# ── Group 17: get_activation_recommendations (all) ────────────────────────────
group("17 — get_activation_recommendations all")
try:
    from app.data.local.model_governance_repo import (
        upsert_activation_recommendation, get_activation_recommendations
    )
    conn = _make_conn()
    for mod in ["strategy_learning", "bankroll", "market_clv"]:
        upsert_activation_recommendation(conn, mod, "OBSERVE_MORE")
    recs = get_activation_recommendations(conn)
    assert_eq("three recs", len(recs), 3)
    modules = [r["module"] for r in recs]
    assert_in("strategy_learning in list", "strategy_learning", modules)
    conn.close()
except Exception as e:
    fail("get_activation_recommendations all", str(e))


# ── Group 18: get_governance_summary ──────────────────────────────────────────
group("18 — get_governance_summary")
try:
    from app.data.local.model_governance_repo import (
        create_experiment, upsert_activation_recommendation, get_governance_summary
    )
    conn = _make_conn()
    create_experiment(conn, "sum_exp_1", "S1", "value_engine", status="active")
    create_experiment(conn, "sum_exp_2", "S2", "bankroll", status="paused")
    upsert_activation_recommendation(conn, "bankroll", "SAFE_TO_TEST_SHADOW")
    summary = get_governance_summary(conn)
    assert_true("experiments key exists", "experiments" in summary)
    assert_true("recommendations key exists", "recommendations" in summary)
    assert_eq("active count", summary["experiments"].get("active", 0), 1)
    assert_eq("bankroll rec", summary["recommendations"].get("bankroll"), "SAFE_TO_TEST_SHADOW")
    conn.close()
except Exception as e:
    fail("get_governance_summary", str(e))


# ── Group 19: DEFAULT_EXPERIMENTS list ────────────────────────────────────────
group("19 — DEFAULT_EXPERIMENTS list")
try:
    from app.data.local.model_governance_repo import DEFAULT_EXPERIMENTS
    assert_eq("6 default experiments", len(DEFAULT_EXPERIMENTS), 6)
    keys = [d["experiment_key"] for d in DEFAULT_EXPERIMENTS]
    for expected in [
        "baseline_current", "variant_value_engine", "variant_strategy_learning",
        "variant_bankroll_conservative", "variant_market_clv", "variant_parlay_filtered"
    ]:
        assert_in(f"key {expected}", expected, keys)
    modules = [d["module"] for d in DEFAULT_EXPERIMENTS]
    assert_in("parlay module", "parlay", modules)
    assert_in("strategy_learning module", "strategy_learning", modules)
except Exception as e:
    fail("DEFAULT_EXPERIMENTS", str(e))


# ── Group 20: compute_confidence_interval ─────────────────────────────────────
group("20 — compute_confidence_interval")
try:
    from app.services.model_governance_service import compute_confidence_interval
    lo, hi = compute_confidence_interval(50, 100)
    assert_true("CI lo < 0.5", lo < 0.5)
    assert_true("CI hi > 0.5", hi > 0.5)
    assert_true("CI lo >= 0", lo >= 0.0)
    assert_true("CI hi <= 1", hi <= 1.0)
    lo2, hi2 = compute_confidence_interval(0, 0)
    assert_eq("empty sample lo", lo2, 0.0)
    assert_eq("empty sample hi", hi2, 1.0)
    lo3, hi3 = compute_confidence_interval(100, 100)
    assert_true("100% hit rate CI hi near 1", hi3 > 0.95)
    lo4, hi4 = compute_confidence_interval(0, 100)
    assert_true("0% hit rate CI lo near 0", lo4 < 0.05)
except Exception as e:
    fail("compute_confidence_interval", str(e))


# ── Group 21: compute_drawdown ────────────────────────────────────────────────
group("21 — compute_drawdown")
try:
    from app.services.model_governance_service import compute_drawdown
    dd = compute_drawdown([1.0, 1.0, -3.0, 1.0])
    assert_approx("drawdown 3.0", dd, 3.0)
    dd2 = compute_drawdown([])
    assert_eq("empty drawdown 0", dd2, 0.0)
    dd3 = compute_drawdown([1.0, 1.0, 1.0])
    assert_eq("no drawdown = 0", dd3, 0.0)
    dd4 = compute_drawdown([-1.0, -1.0, -1.0])
    assert_approx("monotone decline dd=3", dd4, 3.0)
    dd5 = compute_drawdown([2.0, -1.0, 3.0, -2.0])
    assert_approx("mixed drawdown", dd5, 2.0)
except Exception as e:
    fail("compute_drawdown", str(e))


# ── Group 22: compute_variant_lift ────────────────────────────────────────────
group("22 — compute_variant_lift")
try:
    from app.services.model_governance_service import compute_variant_lift
    lift = compute_variant_lift(0.02, 0.05)
    assert_approx("lift 0.03", lift, 0.03)
    lift2 = compute_variant_lift(0.05, 0.02)
    assert_approx("negative lift -0.03", lift2, -0.03)
    lift3 = compute_variant_lift(None, 0.05)
    assert_eq("None baseline -> None", lift3, None)
    lift4 = compute_variant_lift(0.05, None)
    assert_eq("None variant -> None", lift4, None)
    lift5 = compute_variant_lift(0.0, 0.0)
    assert_approx("zero lift", lift5, 0.0)
except Exception as e:
    fail("compute_variant_lift", str(e))


# ── Group 23: evaluate_strategy_gates all pass ────────────────────────────────
group("23 — evaluate_strategy_gates all pass")
try:
    from app.services.model_governance_service import evaluate_strategy_gates
    gates = evaluate_strategy_gates(
        sample_size=250, roi=0.07, baseline_roi=0.03,
        drawdown=2.0, baseline_drawdown=3.0,
        clv_avg=0.015, market_count=4, league_dependency=0.4
    )
    passed = [g for g in gates if g["passed"]]
    failed = [g for g in gates if not g["passed"]]
    assert_eq("6 gates total", len(gates), 6)
    assert_eq("all 6 pass", len(passed), 6)
    assert_eq("none fail", len(failed), 0)
except Exception as e:
    fail("strategy_gates all pass", str(e))


# ── Group 24: evaluate_strategy_gates sample fail ────────────────────────────
group("24 — evaluate_strategy_gates sample insufficient")
try:
    from app.services.model_governance_service import evaluate_strategy_gates
    gates = evaluate_strategy_gates(
        sample_size=150, roi=0.07, baseline_roi=0.03,
        drawdown=2.0, baseline_drawdown=3.0,
        clv_avg=0.015, market_count=4, league_dependency=0.4
    )
    sample_gate = next(g for g in gates if g["gate"] == "min_sample_200")
    assert_false("sample gate fails at 150", sample_gate["passed"])
except Exception as e:
    fail("strategy_gates sample fail", str(e))


# ── Group 25: evaluate_strategy_gates roi fail ────────────────────────────────
group("25 — evaluate_strategy_gates roi lift insufficient")
try:
    from app.services.model_governance_service import evaluate_strategy_gates
    gates = evaluate_strategy_gates(
        sample_size=250, roi=0.04, baseline_roi=0.03,
        drawdown=2.0, baseline_drawdown=3.0,
        clv_avg=0.015, market_count=4, league_dependency=0.4
    )
    roi_gate = next(g for g in gates if g["gate"] == "roi_beats_baseline_3pct")
    assert_false("roi lift 1% fails gate (need 3%)", roi_gate["passed"])
except Exception as e:
    fail("strategy_gates roi fail", str(e))


# ── Group 26: evaluate_strategy_gates clv negative ───────────────────────────
group("26 — evaluate_strategy_gates CLV negative")
try:
    from app.services.model_governance_service import evaluate_strategy_gates
    gates = evaluate_strategy_gates(
        sample_size=250, roi=0.07, baseline_roi=0.03,
        drawdown=2.0, baseline_drawdown=3.0,
        clv_avg=-0.005, market_count=4, league_dependency=0.4
    )
    clv_gate = next(g for g in gates if g["gate"] == "clv_avg_non_negative")
    assert_false("clv negative fails gate", clv_gate["passed"])
except Exception as e:
    fail("strategy_gates clv negative", str(e))


# ── Group 27: evaluate_strategy_gates league dependency ──────────────────────
group("27 — evaluate_strategy_gates league dependency")
try:
    from app.services.model_governance_service import evaluate_strategy_gates
    gates = evaluate_strategy_gates(
        sample_size=250, roi=0.07, baseline_roi=0.03,
        drawdown=2.0, baseline_drawdown=3.0,
        clv_avg=0.015, market_count=4, league_dependency=0.75
    )
    dep_gate = next(g for g in gates if g["gate"] == "no_single_league_dependency")
    assert_false("75% league dep fails gate", dep_gate["passed"])
except Exception as e:
    fail("strategy_gates league dep fail", str(e))


# ── Group 28: evaluate_bankroll_gates all pass ───────────────────────────────
group("28 — evaluate_bankroll_gates all pass")
try:
    from app.services.model_governance_service import evaluate_bankroll_gates
    gates = evaluate_bankroll_gates(
        sample_size=120, roi=0.05, baseline_roi=0.04,
        drawdown=1.5, baseline_drawdown=2.5,
        league_exposure=0.30, market_exposure=0.45,
        picks_maintained=0.85
    )
    assert_eq("6 bankroll gates", len(gates), 6)
    passed = sum(1 for g in gates if g["passed"])
    assert_eq("all 6 pass", passed, 6)
except Exception as e:
    fail("bankroll_gates all pass", str(e))


# ── Group 29: evaluate_bankroll_gates drawdown worse ─────────────────────────
group("29 — evaluate_bankroll_gates drawdown not reduced")
try:
    from app.services.model_governance_service import evaluate_bankroll_gates
    gates = evaluate_bankroll_gates(
        sample_size=120, roi=0.05, baseline_roi=0.04,
        drawdown=3.0, baseline_drawdown=2.0,  # drawdown WORSE
        league_exposure=0.30, market_exposure=0.45, picks_maintained=0.85
    )
    dd_gate = next(g for g in gates if g["gate"] == "reduces_drawdown")
    assert_false("drawdown worse fails gate", dd_gate["passed"])
except Exception as e:
    fail("bankroll_gates drawdown fail", str(e))


# ── Group 30: evaluate_bankroll_gates picks not maintained ────────────────────
group("30 — evaluate_bankroll_gates picks not maintained")
try:
    from app.services.model_governance_service import evaluate_bankroll_gates
    gates = evaluate_bankroll_gates(
        sample_size=120, roi=0.05, baseline_roi=0.04,
        drawdown=1.5, baseline_drawdown=2.5,
        picks_maintained=0.60  # < 0.70
    )
    picks_gate = next(g for g in gates if g["gate"] == "maintains_sufficient_picks")
    assert_false("picks_maintained 60% fails gate", picks_gate["passed"])
except Exception as e:
    fail("bankroll_gates picks fail", str(e))


# ── Group 31: evaluate_market_gates all pass ─────────────────────────────────
group("31 — evaluate_market_gates all pass")
try:
    from app.services.model_governance_service import evaluate_market_gates
    gates = evaluate_market_gates(
        sample_size=200, clv_avg=0.015, clv_beat_rate=0.58,
        roi=0.06, baseline_roi=0.04,
        drawdown=1.8, baseline_drawdown=2.5
    )
    assert_eq("5 market gates", len(gates), 5)
    passed = sum(1 for g in gates if g["passed"])
    assert_eq("all 5 pass", passed, 5)
except Exception as e:
    fail("market_gates all pass", str(e))


# ── Group 32: evaluate_market_gates CLV negative ─────────────────────────────
group("32 — evaluate_market_gates CLV negative")
try:
    from app.services.model_governance_service import evaluate_market_gates
    gates = evaluate_market_gates(
        sample_size=200, clv_avg=-0.003, clv_beat_rate=0.55,
        roi=0.04, baseline_roi=0.04,
        drawdown=2.0, baseline_drawdown=2.5
    )
    clv_gate = next(g for g in gates if g["gate"] == "clv_avg_positive")
    assert_false("clv_avg negative fails", clv_gate["passed"])
except Exception as e:
    fail("market_gates clv fail", str(e))


# ── Group 33: evaluate_parlay_gates all pass ──────────────────────────────────
group("33 — evaluate_parlay_gates all pass")
try:
    from app.services.model_governance_service import evaluate_parlay_gates
    gates = evaluate_parlay_gates(
        sample_size=60, roi=0.02, drawdown=3.0,
        max_stake=0.4, correlation_controlled=True
    )
    assert_eq("5 parlay gates", len(gates), 5)
    passed = sum(1 for g in gates if g["passed"])
    assert_eq("all 5 pass", passed, 5)
except Exception as e:
    fail("parlay_gates all pass", str(e))


# ── Group 34: evaluate_parlay_gates ROI negative ─────────────────────────────
group("34 — evaluate_parlay_gates ROI negative")
try:
    from app.services.model_governance_service import evaluate_parlay_gates
    gates = evaluate_parlay_gates(
        sample_size=60, roi=-0.01, drawdown=3.0,
        max_stake=0.4, correlation_controlled=True
    )
    roi_gate = next(g for g in gates if g["gate"] == "roi_non_negative")
    assert_false("negative roi fails", roi_gate["passed"])
except Exception as e:
    fail("parlay_gates roi fail", str(e))


# ── Group 35: evaluate_parlay_gates max_stake exceeded ───────────────────────
group("35 — evaluate_parlay_gates max_stake too high")
try:
    from app.services.model_governance_service import evaluate_parlay_gates
    gates = evaluate_parlay_gates(
        sample_size=60, roi=0.02, drawdown=3.0,
        max_stake=0.8, correlation_controlled=True  # > 0.5
    )
    stake_gate = next(g for g in gates if g["gate"] == "max_stake_under_0_5u")
    assert_false("max_stake 0.8 fails gate", stake_gate["passed"])
except Exception as e:
    fail("parlay_gates max_stake fail", str(e))


# ── Group 36: gates_to_recommendation all pass -> SAFE_TO_USE ─────────────────
group("36 — gates_to_recommendation all pass")
try:
    from app.services.model_governance_service import gates_to_recommendation, _gate
    gates = [_gate("a", True), _gate("b", True), _gate("c", True)]
    rec, blocking, passed, total = gates_to_recommendation(gates, 250, 200)
    assert_eq("rec all pass", rec, "SAFE_TO_USE_FOR_SELECTION")
    assert_eq("passed=3", passed, 3)
    assert_eq("total=3", total, 3)
    assert_eq("no blocking", blocking, "")
except Exception as e:
    fail("gates_to_recommendation all pass", str(e))


# ── Group 37: gates_to_recommendation insufficient sample ─────────────────────
group("37 — gates_to_recommendation sample insufficient")
try:
    from app.services.model_governance_service import gates_to_recommendation, _gate
    gates = [_gate("a", True), _gate("b", True), _gate("c", True)]
    rec, blocking, passed, total = gates_to_recommendation(gates, 50, 200)
    assert_eq("rec observe_more when sample low", rec, "OBSERVE_MORE")
    assert_true("blocking has text", len(blocking) > 0)
except Exception as e:
    fail("gates_to_recommendation sample insuf", str(e))


# ── Group 38: gates_to_recommendation one failing ─────────────────────────────
group("38 — gates_to_recommendation one failing gate")
try:
    from app.services.model_governance_service import gates_to_recommendation, _gate
    gates = [_gate("a", True), _gate("b", True), _gate("c", False, "c reason")]
    rec, blocking, passed, total = gates_to_recommendation(gates, 300, 200)
    assert_eq("rec assist when 1 failing", rec, "SAFE_TO_TEST_ASSIST")
    assert_true("blocking mentions gate c", "c" in blocking)
except Exception as e:
    fail("gates_to_recommendation one fail", str(e))


# ── Group 39: gates_to_recommendation many failing ───────────────────────────
group("39 — gates_to_recommendation many failing")
try:
    from app.services.model_governance_service import gates_to_recommendation, _gate
    # 2 pass, 4 fail -> < 60%
    gates = [_gate("a", True), _gate("b", True), _gate("c", False),
             _gate("d", False), _gate("e", False), _gate("f", False)]
    rec, blocking, passed, total = gates_to_recommendation(gates, 300, 200)
    assert_eq("rec observe_more when many fail", rec, "OBSERVE_MORE")
except Exception as e:
    fail("gates_to_recommendation many fail", str(e))


# ── Group 40: gates_to_recommendation zero gates ──────────────────────────────
group("40 — gates_to_recommendation no gates at all")
try:
    from app.services.model_governance_service import gates_to_recommendation
    rec, blocking, passed, total = gates_to_recommendation([], 300, 200)
    assert_eq("rec DO_NOT_ACTIVATE with no gates", rec, "DO_NOT_ACTIVATE")
    assert_eq("passed=0", passed, 0)
    assert_eq("total=0", total, 0)
except Exception as e:
    fail("gates_to_recommendation no gates", str(e))


# ── Group 41: REC_RANK ordering ───────────────────────────────────────────────
group("41 — recommendation rank ordering")
try:
    from app.services.model_governance_service import _REC_RANK
    assert_true("DO_NOT < OBSERVE", _REC_RANK["DO_NOT_ACTIVATE"] < _REC_RANK["OBSERVE_MORE"])
    assert_true("OBSERVE < SHADOW", _REC_RANK["OBSERVE_MORE"] < _REC_RANK["SAFE_TO_TEST_SHADOW"])
    assert_true("SHADOW < ASSIST", _REC_RANK["SAFE_TO_TEST_SHADOW"] < _REC_RANK["SAFE_TO_TEST_ASSIST"])
    assert_true("ASSIST < SELECT", _REC_RANK["SAFE_TO_TEST_ASSIST"] < _REC_RANK["SAFE_TO_USE_FOR_SELECTION"])
except Exception as e:
    fail("rec rank ordering", str(e))


# ── Group 42: record_decision_audit disabled ──────────────────────────────────
group("42 — record_decision_audit disabled (no-op)")
try:
    from app.services.model_governance_service import record_decision_audit
    conn = _make_conn()
    record_decision_audit(conn, {"id": 9001}, official_selected=True, enabled=False)
    r = conn.execute("SELECT COUNT(*) FROM model_decision_audit").fetchone()
    assert_eq("no rows written when disabled", r[0], 0)
    conn.close()
except Exception as e:
    fail("record_decision_audit disabled", str(e))


# ── Group 43: record_decision_audit enabled ───────────────────────────────────
group("43 — record_decision_audit enabled")
try:
    from app.services.model_governance_service import record_decision_audit
    conn = _make_conn()
    pick = {
        "id": 9002, "fixture_id": 200, "market_key": "BTTS",
        "league_id": 5, "edge": 0.06, "quality_score": 72.0,
        "bankroll_recommended_units": 0.9, "bankroll_risk_label": "low",
    }
    record_decision_audit(conn, pick, official_selected=True, enabled=True)
    r = conn.execute("SELECT COUNT(*) FROM model_decision_audit").fetchone()
    assert_eq("one row written when enabled", r[0], 1)
    conn.close()
except Exception as e:
    fail("record_decision_audit enabled", str(e))


# ── Group 44: run_experiment_lab dry-run (no writes) ─────────────────────────
group("44 — run_experiment_lab dry_run writes nothing")
try:
    from app.data.local.model_governance_repo import create_experiment
    from app.services.model_governance_service import run_experiment_lab
    conn = _make_conn()
    create_experiment(conn, "baseline_current", "Baseline", "value_engine", min_sample=50)
    results = run_experiment_lab(conn, days=30, experiment_key="baseline_current", dry_run=True)
    r = conn.execute("SELECT COUNT(*) FROM experiment_results").fetchone()
    assert_eq("no rows written in dry_run", r[0], 0)
    assert_eq("one result returned", len(results), 1)
    conn.close()
except Exception as e:
    fail("run_experiment_lab dry_run", str(e))


# ── Group 45: run_experiment_lab execute writes results ──────────────────────
group("45 — run_experiment_lab execute writes results")
try:
    from app.data.local.model_governance_repo import create_experiment
    from app.services.model_governance_service import run_experiment_lab
    conn = _make_conn()
    create_experiment(conn, "baseline_current", "Baseline", "value_engine", min_sample=50)
    create_experiment(conn, "variant_parlay_filtered", "Parlay", "parlay", min_sample=50)
    results = run_experiment_lab(conn, days=30, dry_run=False)
    r = conn.execute("SELECT COUNT(*) FROM experiment_results").fetchone()
    assert_eq("2 result rows written", r[0], 2)
    conn.close()
except Exception as e:
    fail("run_experiment_lab execute", str(e))


# ── Group 46: run_experiment_lab recommendation logic ────────────────────────
group("46 — run_experiment_lab result fields")
try:
    from app.data.local.model_governance_repo import create_experiment
    from app.services.model_governance_service import run_experiment_lab
    conn = _make_conn()
    create_experiment(conn, "baseline_current", "Baseline", "value_engine", min_sample=50)
    results = run_experiment_lab(conn, days=30, experiment_key="baseline_current", dry_run=True)
    r = results[0]
    assert_in("experiment_key", "experiment_key", r)
    assert_in("recommendation", "recommendation", r)
    assert_in("sample_size", "sample_size", r)
    assert_in("gates_passed", "gates_passed", r)
    assert_in("gates_total", "gates_total", r)
    conn.close()
except Exception as e:
    fail("run_experiment_lab fields", str(e))


# ── Group 47: run_experiment_lab strategy module gates ───────────────────────
group("47 — run_experiment_lab strategy module gate count")
try:
    from app.data.local.model_governance_repo import create_experiment
    from app.services.model_governance_service import run_experiment_lab
    conn = _make_conn()
    create_experiment(conn, "variant_strategy_learning", "SL", "strategy_learning", min_sample=200)
    results = run_experiment_lab(conn, days=30, experiment_key="variant_strategy_learning", dry_run=True)
    r = results[0]
    assert_eq("6 strategy gates", r["gates_total"], 6)
except Exception as e:
    fail("run_experiment_lab strategy gates", str(e))


# ── Group 48: run_experiment_lab bankroll module gates ────────────────────────
group("48 — run_experiment_lab bankroll module gate count")
try:
    from app.data.local.model_governance_repo import create_experiment
    from app.services.model_governance_service import run_experiment_lab
    conn = _make_conn()
    create_experiment(conn, "variant_bankroll_conservative", "BR", "bankroll", min_sample=100)
    results = run_experiment_lab(conn, days=30, experiment_key="variant_bankroll_conservative", dry_run=True)
    r = results[0]
    assert_eq("6 bankroll gates", r["gates_total"], 6)
except Exception as e:
    fail("run_experiment_lab bankroll gates", str(e))


# ── Group 49: run_experiment_lab market module gates ─────────────────────────
group("49 — run_experiment_lab market module gate count")
try:
    from app.data.local.model_governance_repo import create_experiment
    from app.services.model_governance_service import run_experiment_lab
    conn = _make_conn()
    create_experiment(conn, "variant_market_clv", "MKT", "market_clv", min_sample=150)
    results = run_experiment_lab(conn, days=30, experiment_key="variant_market_clv", dry_run=True)
    r = results[0]
    assert_eq("5 market gates", r["gates_total"], 5)
except Exception as e:
    fail("run_experiment_lab market gates", str(e))


# ── Group 50: run_experiment_lab parlay module gates ─────────────────────────
group("50 — run_experiment_lab parlay module gate count")
try:
    from app.data.local.model_governance_repo import create_experiment
    from app.services.model_governance_service import run_experiment_lab
    conn = _make_conn()
    create_experiment(conn, "variant_parlay_filtered", "PAR", "parlay", min_sample=50)
    results = run_experiment_lab(conn, days=30, experiment_key="variant_parlay_filtered", dry_run=True)
    r = results[0]
    assert_eq("5 parlay gates", r["gates_total"], 5)
except Exception as e:
    fail("run_experiment_lab parlay gates", str(e))


# ── Group 51: run_experiment_lab activation_recommendations written ───────────
group("51 — run_experiment_lab writes activation_recommendations")
try:
    from app.data.local.model_governance_repo import create_experiment, get_activation_recommendation
    from app.services.model_governance_service import run_experiment_lab
    conn = _make_conn()
    create_experiment(conn, "variant_strategy_learning", "SL", "strategy_learning", min_sample=200)
    run_experiment_lab(conn, days=30, experiment_key="variant_strategy_learning", dry_run=False)
    rec = get_activation_recommendation(conn, "strategy_learning")
    assert_true("activation rec written", rec is not None)
    assert_in("recommendation field", "recommendation", rec)
except Exception as e:
    fail("run_experiment_lab writes activation_recs", str(e))


# ── Group 52: run_governance_job registers defaults ──────────────────────────
group("52 — run_governance_job registers DEFAULT_EXPERIMENTS")
try:
    from app.services.model_governance_service import run_governance_job
    conn = _make_conn()
    outcome = run_governance_job(conn, days=30, dry_run=True)
    r = conn.execute("SELECT COUNT(*) FROM experiment_registry WHERE status='active'").fetchone()
    assert_eq("6 active experiments registered", r[0], 6)
    assert_eq("experiments_processed in outcome", outcome["experiments_processed"], 6)
except Exception as e:
    fail("run_governance_job registers defaults", str(e))


# ── Group 53: run_governance_job dry_run does not write results ───────────────
group("53 — run_governance_job dry_run no result writes")
try:
    from app.services.model_governance_service import run_governance_job
    conn = _make_conn()
    run_governance_job(conn, days=30, dry_run=True)
    r = conn.execute("SELECT COUNT(*) FROM experiment_results").fetchone()
    assert_eq("no results written in dry_run", r[0], 0)
    conn.close()
except Exception as e:
    fail("run_governance_job dry_run no writes", str(e))


# ── Group 54: run_governance_job execute writes all tables ────────────────────
group("54 — run_governance_job execute writes results")
try:
    from app.services.model_governance_service import run_governance_job
    conn = _make_conn()
    run_governance_job(conn, days=30, dry_run=False)
    r = conn.execute("SELECT COUNT(*) FROM experiment_results").fetchone()
    assert_gte("experiment_results written", r[0], 1)
    conn.close()
except Exception as e:
    fail("run_governance_job execute writes", str(e))


# ── Group 55: Config — model_governance_enabled default false ─────────────────
group("55 — config defaults")
try:
    from app.core.config import settings
    assert_false("model_governance_enabled defaults false", settings.model_governance_enabled)
    assert_false("experiments_enabled defaults false", settings.model_governance_experiments_enabled)
    assert_true("decision_audit defaults true", settings.model_governance_decision_audit_enabled)
    assert_false("auto_activate defaults false", settings.model_governance_auto_activate)
    assert_true("write_to_duckdb defaults true", settings.model_governance_write_to_duckdb)
except Exception as e:
    fail("config defaults", str(e))


# ── Group 56: Config — sample minimums ───────────────────────────────────────
group("56 — config sample minimums")
try:
    from app.core.config import settings
    assert_eq("min_sample_strategy 200", settings.model_governance_min_sample_strategy, 200)
    assert_eq("min_sample_bankroll 100", settings.model_governance_min_sample_bankroll, 100)
    assert_eq("min_sample_market 150", settings.model_governance_min_sample_market, 150)
    assert_eq("min_sample_parlay 50", settings.model_governance_min_sample_parlay, 50)
except Exception as e:
    fail("config sample minimums", str(e))


# ── Group 57: Config — scheduler governance ──────────────────────────────────
group("57 — config scheduler governance")
try:
    from app.core.config import settings
    assert_false("scheduler_governance_enabled defaults false", settings.scheduler_governance_enabled)
    assert_eq("scheduler_governance_time 23:30", settings.scheduler_governance_time, "23:30")
except Exception as e:
    fail("config scheduler governance", str(e))


# ── Group 58: register_experiments.py script exists ──────────────────────────
group("58 — register_experiments.py script")
try:
    path = Path(__file__).parent / "register_experiments.py"
    assert_true("file exists", path.exists())
    src = path.read_text(encoding="utf-8")
    assert_in("--execute flag", "--execute", src)
    assert_in("--dry-run flag", "--dry-run", src)
    assert_in("--reset-defaults flag", "--reset-defaults", src)
    assert_in("create_experiment call", "create_experiment", src)
except Exception as e:
    fail("register_experiments.py", str(e))


# ── Group 59: run_experiment_lab.py script exists ────────────────────────────
group("59 — run_experiment_lab.py script")
try:
    path = Path(__file__).parent / "run_experiment_lab.py"
    assert_true("file exists", path.exists())
    src = path.read_text(encoding="utf-8")
    assert_in("--days flag", "--days", src)
    assert_in("--experiment flag", "--experiment", src)
    assert_in("--all flag", "--all", src)
    assert_in("run_experiment_lab import", "run_experiment_lab", src)
except Exception as e:
    fail("run_experiment_lab.py", str(e))


# ── Group 60: audit_model_governance.py script exists ────────────────────────
group("60 — audit_model_governance.py script")
try:
    path = Path(__file__).parent / "audit_model_governance.py"
    assert_true("file exists", path.exists())
    src = path.read_text(encoding="utf-8")
    for tbl in ["experiment_registry", "experiment_results", "model_decision_audit",
                "activation_recommendations"]:
        assert_in(f"table {tbl} referenced", tbl, src)
except Exception as e:
    fail("audit_model_governance.py", str(e))


# ── Group 61: report_experiments.py script exists ────────────────────────────
group("61 — report_experiments.py script")
try:
    path = Path(__file__).parent / "report_experiments.py"
    assert_true("file exists", path.exists())
    src = path.read_text(encoding="utf-8")
    assert_in("--days flag", "--days", src)
    assert_in("--detail flag", "--detail", src)
    assert_in("--summary flag", "--summary", src)
    assert_in("get_experiment_results", "get_experiment_results", src)
except Exception as e:
    fail("report_experiments.py", str(e))


# ── Group 62: report_activation_readiness.py script exists ───────────────────
group("62 — report_activation_readiness.py script")
try:
    path = Path(__file__).parent / "report_activation_readiness.py"
    assert_true("file exists", path.exists())
    src = path.read_text(encoding="utf-8")
    for module in ["strategy_learning", "bankroll", "market_clv", "parlay"]:
        assert_in(f"module {module}", module, src)
    assert_in("MODEL_GOVERNANCE_AUTO_ACTIVATE", "MODEL_GOVERNANCE_AUTO_ACTIVATE", src)
except Exception as e:
    fail("report_activation_readiness.py", str(e))


# ── Group 63: gobernanza.py handler exists ────────────────────────────────────
group("63 — gobernanza.py handler exists")
try:
    path = Path(__file__).parent.parent / "app" / "bot" / "handlers" / "gobernanza.py"
    assert_true("file exists", path.exists())
    src = path.read_text(encoding="utf-8")
    assert_in("gobernanza_handler defined", "async def gobernanza_handler", src)
    assert_in("experimentos_handler defined", "async def experimentos_handler", src)
    assert_in("activar_handler defined", "async def activar_handler", src)
except Exception as e:
    fail("gobernanza.py handler", str(e))


# ── Group 64: ai_router_service has governance intents ───────────────────────
group("64 — ai_router_service governance intents")
try:
    path = Path(__file__).parent.parent / "app" / "services" / "ai_router_service.py"
    src = path.read_text(encoding="utf-8")
    for intent in ["governance_summary", "experiment_summary", "activation_readiness",
                   "model_comparison", "why_not_activate", "safe_mode_help"]:
        assert_in(f"intent {intent}", intent, src)
except Exception as e:
    fail("ai_router governance intents", str(e))


# ── Group 65: conversation.py routes governance intents ──────────────────────
group("65 — conversation.py governance routing")
try:
    path = Path(__file__).parent.parent / "app" / "bot" / "handlers" / "conversation.py"
    src = path.read_text(encoding="utf-8")
    assert_in("governance_summary routing", "governance_summary", src)
    assert_in("experiment_summary routing", "experiment_summary", src)
    assert_in("activation_readiness routing", "activation_readiness", src)
    assert_in("gobernanza_handler import", "gobernanza_handler", src)
except Exception as e:
    fail("conversation.py governance routing", str(e))


# ── Group 66: scheduled_jobs.py has model_governance_job ─────────────────────
group("66 — scheduled_jobs.py model_governance_job")
try:
    path = Path(__file__).parent.parent / "app" / "services" / "scheduled_jobs.py"
    src = path.read_text(encoding="utf-8")
    assert_in("model_governance_job defined", "async def model_governance_job", src)
    assert_in("governance_enabled check", "scheduler_governance_enabled", src)
    assert_in("run_governance_job call", "run_governance_job", src)
except Exception as e:
    fail("scheduled_jobs model_governance_job", str(e))


# ── Group 67: scheduler_service.py registers governance_job ──────────────────
group("67 — scheduler_service.py governance registration")
try:
    path = Path(__file__).parent.parent / "app" / "services" / "scheduler_service.py"
    src = path.read_text(encoding="utf-8")
    assert_in("model_governance_job import", "model_governance_job", src)
    assert_in("governance_enabled guard", "scheduler_governance_enabled", src)
except Exception as e:
    fail("scheduler_service governance registration", str(e))


# ── Group 68: main.py has gobernanza handlers ─────────────────────────────────
group("68 — main.py gobernanza handlers")
try:
    path = Path(__file__).parent.parent / "app" / "main.py"
    src = path.read_text(encoding="utf-8")
    assert_in("gobernanza command", "gobernanza", src)
    assert_in("experimentos command", "experimentos", src)
    assert_in("activar command", "activar", src)
except Exception as e:
    fail("main.py gobernanza handlers", str(e))


# ── Group 69: init_local_db.py has 5 governance tables ───────────────────────
group("69 — init_local_db.py governance tables")
try:
    path = Path(__file__).parent / "init_local_db.py"
    src = path.read_text(encoding="utf-8")
    for tbl in ["experiment_registry", "experiment_pick_assignments",
                "experiment_results", "model_decision_audit",
                "activation_recommendations"]:
        assert_in(f"table {tbl}", tbl, src)
except Exception as e:
    fail("init_local_db governance tables", str(e))


# ── Group 70: report_daily_edge.py has governance section ────────────────────
group("70 — report_daily_edge.py governance section")
try:
    path = Path(__file__).parent / "report_daily_edge.py"
    src = path.read_text(encoding="utf-8")
    assert_in("governance section", "governance", src.lower())
except Exception as e:
    fail("report_daily_edge governance section", str(e))


# ── Group 71: model_governance_repo.py imports only stdlib + duckdb ───────────
group("71 — repo module stdlib imports only")
try:
    path = Path(__file__).parent.parent / "app" / "data" / "local" / "model_governance_repo.py"
    src = path.read_text(encoding="utf-8")
    assert_false("no telegram import", "import telegram" in src)
    assert_false("no app.bot import", "from app.bot" in src)
    assert_in("json import", "import json", src)
    assert_in("logging import", "import logging", src)
except Exception as e:
    fail("repo stdlib imports", str(e))


# ── Group 72: service module no async ─────────────────────────────────────────
group("72 — service module functions are sync")
try:
    from app.services import model_governance_service as svc
    sync_funcs = [
        "compute_confidence_interval",
        "compute_drawdown",
        "compute_variant_lift",
        "evaluate_strategy_gates",
        "evaluate_bankroll_gates",
        "evaluate_market_gates",
        "evaluate_parlay_gates",
        "gates_to_recommendation",
        "run_experiment_lab",
        "run_governance_job",
    ]
    for fn_name in sync_funcs:
        fn = getattr(svc, fn_name, None)
        assert_true(f"{fn_name} exists", fn is not None)
        assert_false(f"{fn_name} is not async", inspect.iscoroutinefunction(fn))
except Exception as e:
    fail("service sync functions", str(e))


# ── Group 73: CI widens with lower sample ─────────────────────────────────────
group("73 — confidence interval wider with smaller sample")
try:
    from app.services.model_governance_service import compute_confidence_interval
    lo_small, hi_small = compute_confidence_interval(25, 50)
    lo_large, hi_large = compute_confidence_interval(250, 500)
    width_small = hi_small - lo_small
    width_large = hi_large - lo_large
    assert_true("smaller sample has wider CI", width_small > width_large)
except Exception as e:
    fail("CI width comparison", str(e))


# ── Group 74: drawdown monotone series ────────────────────────────────────────
group("74 — drawdown edge cases")
try:
    from app.services.model_governance_service import compute_drawdown
    dd = compute_drawdown([0.0, 0.0, 0.0])
    assert_eq("all zeros drawdown 0", dd, 0.0)
    dd2 = compute_drawdown([10.0])
    assert_eq("single positive no drawdown", dd2, 0.0)
    dd3 = compute_drawdown([-10.0])
    assert_approx("single negative dd=10", dd3, 10.0)
except Exception as e:
    fail("drawdown edge cases", str(e))


# ── Group 75: strategy_gates market count ─────────────────────────────────────
group("75 — strategy gates market count boundary")
try:
    from app.services.model_governance_service import evaluate_strategy_gates
    gates_2 = evaluate_strategy_gates(250, 0.07, 0.03, 2.0, 3.0, 0.015, market_count=2)
    gates_3 = evaluate_strategy_gates(250, 0.07, 0.03, 2.0, 3.0, 0.015, market_count=3)
    mkt2 = next(g for g in gates_2 if g["gate"] == "min_3_markets")
    mkt3 = next(g for g in gates_3 if g["gate"] == "min_3_markets")
    assert_false("market_count=2 fails gate", mkt2["passed"])
    assert_true("market_count=3 passes gate", mkt3["passed"])
except Exception as e:
    fail("strategy gates market count boundary", str(e))


# ── Group 76: bankroll gates league exposure boundary ────────────────────────
group("76 — bankroll gates league exposure boundary")
try:
    from app.services.model_governance_service import evaluate_bankroll_gates
    g_ok  = evaluate_bankroll_gates(120, 0.05, 0.04, 1.5, 2.5, league_exposure=0.49)
    g_bad = evaluate_bankroll_gates(120, 0.05, 0.04, 1.5, 2.5, league_exposure=0.51)
    gate_ok  = next(g for g in g_ok  if g["gate"] == "low_league_exposure")
    gate_bad = next(g for g in g_bad if g["gate"] == "low_league_exposure")
    assert_true("49% league ok", gate_ok["passed"])
    assert_false("51% league fail", gate_bad["passed"])
except Exception as e:
    fail("bankroll league exposure boundary", str(e))


# ── Group 77: market gates CLV beat rate boundary ────────────────────────────
group("77 — market gates CLV beat rate boundary")
try:
    from app.services.model_governance_service import evaluate_market_gates
    g_ok  = evaluate_market_gates(200, 0.01, 0.50, 0.05, 0.04, 2.0, 2.5)
    g_bad = evaluate_market_gates(200, 0.01, 0.49, 0.05, 0.04, 2.0, 2.5)
    gate_ok  = next(g for g in g_ok  if g["gate"] == "clv_beat_rate_over_50pct")
    gate_bad = next(g for g in g_bad if g["gate"] == "clv_beat_rate_over_50pct")
    assert_true("beat_rate=0.50 passes", gate_ok["passed"])
    assert_false("beat_rate=0.49 fails", gate_bad["passed"])
except Exception as e:
    fail("market gates clv beat rate boundary", str(e))


# ── Group 78: parlay gates drawdown limit ─────────────────────────────────────
group("78 — parlay gates drawdown limit")
try:
    from app.services.model_governance_service import evaluate_parlay_gates
    g_ok  = evaluate_parlay_gates(60, 0.02, 5.0, 0.4, True)
    g_bad = evaluate_parlay_gates(60, 0.02, 5.1, 0.4, True)
    gate_ok  = next(g for g in g_ok  if g["gate"] == "drawdown_within_limit")
    gate_bad = next(g for g in g_bad if g["gate"] == "drawdown_within_limit")
    assert_true("drawdown 5.0 ok", gate_ok["passed"])
    assert_false("drawdown 5.1 fails", gate_bad["passed"])
except Exception as e:
    fail("parlay drawdown limit", str(e))


# ── Group 79: experiment unique (pick_candidate_id, experiment_key) ───────────
group("79 — experiment_pick_assignments unique constraint")
try:
    from app.data.local.model_governance_repo import upsert_experiment_assignment
    conn = _make_conn()
    upsert_experiment_assignment(conn, 7001, "baseline_current", shadow_edge=0.03)
    upsert_experiment_assignment(conn, 7001, "baseline_current", shadow_edge=0.07)
    r = conn.execute(
        "SELECT COUNT(*) FROM experiment_pick_assignments WHERE pick_candidate_id=7001"
    ).fetchone()
    assert_eq("one row despite two upserts", r[0], 1)
    v = conn.execute(
        "SELECT shadow_edge FROM experiment_pick_assignments WHERE pick_candidate_id=7001"
    ).fetchone()[0]
    assert_approx("edge updated to 0.07", v, 0.07)
    conn.close()
except Exception as e:
    fail("assignments unique constraint", str(e))


# ── Group 80: experiment_results unique (key, window) ────────────────────────
group("80 — experiment_results unique (key, days_window)")
try:
    from app.data.local.model_governance_repo import upsert_experiment_result
    conn = _make_conn()
    upsert_experiment_result(conn, "baseline_current", 30, sample_size=50)
    upsert_experiment_result(conn, "baseline_current", 30, sample_size=80)
    upsert_experiment_result(conn, "baseline_current", 7, sample_size=20)
    r30 = conn.execute(
        "SELECT COUNT(*) FROM experiment_results WHERE experiment_key='baseline_current' AND days_window=30"
    ).fetchone()
    assert_eq("one row for (key, 30)", r30[0], 1)
    r7 = conn.execute(
        "SELECT COUNT(*) FROM experiment_results WHERE experiment_key='baseline_current' AND days_window=7"
    ).fetchone()
    assert_eq("one row for (key, 7)", r7[0], 1)
    conn.close()
except Exception as e:
    fail("experiment_results unique key+window", str(e))


# ── Final results ──────────────────────────────────────────────────────────────
print(f"\n{'=' * 55}")
print(f"  RESULTS: {_passed} passed, {_failed} failed")
print(f"{'=' * 55}\n")

if _failed:
    sys.exit(1)
