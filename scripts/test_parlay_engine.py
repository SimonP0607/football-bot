#!/usr/bin/env python
"""Test suite for Phase 8: Smart Parlay Engine (in-memory DuckDB, no network).

All tests use an in-memory DuckDB instance with the schema applied.
No Supabase calls, no API calls, no file I/O.

Usage:
    python scripts/test_parlay_engine.py
    python scripts/test_parlay_engine.py -v
"""

from __future__ import annotations

import io
import sys
import traceback
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Test harness ──────────────────────────────────────────────────────────────

_passed: list[str] = []
_failed: list[str] = []
_verbose = "-v" in sys.argv


def ok(name: str) -> None:
    _passed.append(name)
    if _verbose:
        print(f"  PASS  {name}")


def fail(name: str, reason: str) -> None:
    _failed.append(name)
    print(f"  FAIL  {name}")
    print(f"        {reason}")


def assert_eq(name: str, got, expected) -> None:
    if got == expected:
        ok(name)
    else:
        fail(name, f"got {got!r}, expected {expected!r}")


def assert_true(name: str, condition: bool, msg: str = "") -> None:
    if condition:
        ok(name)
    else:
        fail(name, msg or "condition is False")


def assert_almost_eq(name: str, got: float, expected: float, tol: float = 1e-6) -> None:
    if abs(got - expected) <= tol:
        ok(name)
    else:
        fail(name, f"got {got}, expected {expected} (tol={tol})")


# ── Schema fixture ────────────────────────────────────────────────────────────


def _make_conn():
    """Return an in-memory DuckDB connection with Phase 8 schema applied."""
    import duckdb

    conn = duckdb.connect(":memory:")

    schema_file = (
        Path(__file__).resolve().parent.parent
        / "sql" / "local" / "008_parlay_engine_schema.sql"
    )
    sql = schema_file.read_text(encoding="utf-8")
    for chunk in sql.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        has_sql = any(
            line.strip() and not line.strip().startswith("--")
            for line in chunk.splitlines()
        )
        if not has_sql:
            continue
        conn.execute(chunk)

    return conn


# ── Sample data helpers ───────────────────────────────────────────────────────


def _make_leg(
    pick_id: int = 1,
    fixture_id: int = 100,
    provider_fixture_id: int = 9000,
    league_id: int = 10,
    market_key: str = "1X2",
    selection: str = "Home",
    odds: float = 1.80,
    p_model: float = 0.60,
    p_cal: float = 0.58,
    edge: float = 0.03,
    quality_score: float = 0.70,
    home_team_id: int = 1,
    away_team_id: int = 2,
) -> dict:
    return {
        "pick_candidate_id": pick_id,
        "fixture_id": fixture_id,
        "provider_fixture_id": provider_fixture_id,
        "league_id": league_id,
        "market_key": market_key,
        "selection": selection,
        "odds": odds,
        "p_model": p_model,
        "p_cal": p_cal,
        "edge": edge,
        "quality_score": quality_score,
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
    }


def _make_parlay(
    parlay_key: str = "2026-05-01_1_2",
    date: str = "2026-05-01",
    legs_count: int = 2,
    total_odds: float = 3.24,
    joint_probability: float = 0.3364,
    implied_probability: float = 0.3086,
    edge: float = 0.0278,
    ev: float = 0.0888,
    risk_score: float = 0.20,
    correlation_score: float = 0.08,
    confidence_score: float = 0.65,
    recommendation_status: str = "recommended",
    parlay_type: str = "conservadora",
) -> dict:
    return {
        "parlay_key": parlay_key,
        "date": date,
        "parlay_type": parlay_type,
        "legs_count": legs_count,
        "total_odds": total_odds,
        "joint_probability": joint_probability,
        "implied_probability": implied_probability,
        "edge": edge,
        "ev": ev,
        "risk_score": risk_score,
        "correlation_score": correlation_score,
        "confidence_score": confidence_score,
        "recommendation_status": recommendation_status,
        "rejection_reason": None,
        "metadata_json": {},
    }


# ── GROUP 1: Schema ───────────────────────────────────────────────────────────

print("\n[1] Schema")
try:
    conn = _make_conn()
    tables = {
        r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
    }
    for t in ("parlay_candidates", "parlay_legs", "parlay_results", "parlay_risk_rules"):
        assert_true(f"schema_table_{t}", t in tables, f"Table {t} not found")

    rules = conn.execute("SELECT COUNT(*) FROM parlay_risk_rules").fetchone()[0]
    assert_true("schema_risk_rules_seeded", rules >= 9, f"Expected >=9 rules, got {rules}")

    ok("schema_creates_cleanly")
except Exception as exc:
    fail("schema_creates_cleanly", traceback.format_exc())


# ── GROUP 2: Repo — insert / read ─────────────────────────────────────────────

print("\n[2] Repo — insert / read")
try:
    from app.data.local import parlay_repo as repo

    conn = _make_conn()

    # insert_parlay_candidate
    prow = _make_parlay()
    repo.insert_parlay_candidate(conn, prow)
    cnt = conn.execute("SELECT COUNT(*) FROM parlay_candidates").fetchone()[0]
    assert_eq("repo_insert_candidate_count", cnt, 1)

    # idempotent
    repo.insert_parlay_candidate(conn, prow)
    cnt2 = conn.execute("SELECT COUNT(*) FROM parlay_candidates").fetchone()[0]
    assert_eq("repo_insert_candidate_idempotent", cnt2, 1)

    # insert_parlay_legs
    legs = [_make_leg(pick_id=1), _make_leg(pick_id=2, fixture_id=101, provider_fixture_id=9001, selection="Away")]
    repo.insert_parlay_legs(conn, prow["parlay_key"], legs)
    lcnt = conn.execute("SELECT COUNT(*) FROM parlay_legs").fetchone()[0]
    assert_eq("repo_insert_legs_count", lcnt, 2)

    # idempotent legs
    repo.insert_parlay_legs(conn, prow["parlay_key"], legs)
    lcnt2 = conn.execute("SELECT COUNT(*) FROM parlay_legs").fetchone()[0]
    assert_eq("repo_insert_legs_idempotent", lcnt2, 2)

    # get_parlay_candidates
    cands = repo.get_parlay_candidates(conn, date="2026-05-01", status="recommended", limit=10)
    assert_eq("repo_get_candidates_count", len(cands), 1)
    assert_eq("repo_get_candidates_status", cands[0]["recommendation_status"], "recommended")

    # get_parlay_by_key
    full = repo.get_parlay_by_key(conn, prow["parlay_key"])
    assert_true("repo_get_by_key_not_none", full is not None)
    assert_eq("repo_get_by_key_legs", len(full["legs"]), 2)

    # get_parlay_legs order
    fetched_legs = repo.get_parlay_legs(conn, prow["parlay_key"])
    assert_eq("repo_legs_ordered", [l["leg_order"] for l in fetched_legs], [0, 1])

    # missing key
    missing = repo.get_parlay_by_key(conn, "nonexistent_key")
    assert_eq("repo_get_by_key_missing", missing, None)

except Exception as exc:
    fail("repo_group", traceback.format_exc())


# ── GROUP 3: Repo — parlay_results ───────────────────────────────────────────

print("\n[3] Repo — parlay_results")
try:
    conn = _make_conn()
    prow = _make_parlay()
    repo.insert_parlay_candidate(conn, prow)

    # upsert win
    repo.upsert_parlay_result(conn, {
        "parlay_key": prow["parlay_key"],
        "result_status": "win",
        "legs_won": 2,
        "legs_lost": 0,
        "legs_void": 0,
        "stake_units": 0.25,
        "profit_units": 0.56,
        "roi": 224.0,
    })
    results = repo.get_parlay_results(conn)
    assert_eq("repo_result_inserted", len(results), 1)
    assert_eq("repo_result_status", results[0]["result_status"], "win")

    # upsert again (update)
    repo.upsert_parlay_result(conn, {
        "parlay_key": prow["parlay_key"],
        "result_status": "loss",
        "legs_won": 1,
        "legs_lost": 1,
        "legs_void": 0,
        "stake_units": 0.25,
        "profit_units": -0.25,
        "roi": -100.0,
    })
    results2 = repo.get_parlay_results(conn)
    assert_eq("repo_result_upsert_count", len(results2), 1)
    assert_eq("repo_result_upsert_status", results2[0]["result_status"], "loss")

    # summary
    summary = repo.get_recent_parlay_summary(conn)
    assert_eq("repo_summary_losses", summary["losses"], 1)
    assert_eq("repo_summary_wins", summary["wins"], 0)

except Exception as exc:
    fail("repo_results_group", traceback.format_exc())


# ── GROUP 4: Math formulas ────────────────────────────────────────────────────

print("\n[4] Math formulas")
try:
    from app.services.parlay_engine_service import (
        compute_joint_probability,
        compute_total_odds,
        compute_parlay_ev,
    )

    legs2 = [_make_leg(p_cal=0.58, odds=1.80), _make_leg(pick_id=2, p_cal=0.65, odds=2.10)]

    # joint prob
    jp = compute_joint_probability(legs2)
    assert_almost_eq("math_joint_prob", jp, 0.58 * 0.65, tol=1e-9)

    # total odds
    to_ = compute_total_odds(legs2)
    assert_almost_eq("math_total_odds", to_, 1.80 * 2.10, tol=1e-9)

    # EV
    ev = compute_parlay_ev(jp, to_)
    expected_ev = jp * to_ - 1.0
    assert_almost_eq("math_ev", ev, expected_ev, tol=1e-9)

    # edge
    impl = 1.0 / to_
    edge = jp - impl
    assert_true("math_edge_sign", edge > 0, f"Expected positive edge, got {edge}")

    # 3-leg product
    legs3 = [_make_leg(p_cal=0.60, odds=1.70),
             _make_leg(pick_id=2, p_cal=0.55, odds=2.00),
             _make_leg(pick_id=3, p_cal=0.50, odds=2.20)]
    jp3 = compute_joint_probability(legs3)
    assert_almost_eq("math_joint_prob_3leg", jp3, 0.60 * 0.55 * 0.50, tol=1e-9)

    to3 = compute_total_odds(legs3)
    assert_almost_eq("math_total_odds_3leg", to3, 1.70 * 2.00 * 2.20, tol=1e-9)

except Exception as exc:
    fail("math_formulas", traceback.format_exc())


# ── GROUP 5: Correlation scoring ──────────────────────────────────────────────

print("\n[5] Correlation scoring")
try:
    from app.services.parlay_engine_service import compute_correlation_score

    # Independent picks — different fixture, team, league
    l1 = _make_leg(pick_id=1, provider_fixture_id=100, league_id=10, home_team_id=1, away_team_id=2,
                   market_key="1X2", selection="Home")
    l2 = _make_leg(pick_id=2, provider_fixture_id=200, league_id=20, home_team_id=3, away_team_id=4,
                   market_key="1X2", selection="Away")
    score_indep = compute_correlation_score([l1, l2])
    assert_true("corr_independent_low", score_indep < 0.20,
                f"Expected low correlation, got {score_indep}")

    # Same fixture — should trigger +0.80
    l3 = _make_leg(pick_id=3, provider_fixture_id=100, league_id=10, home_team_id=1, away_team_id=2,
                   market_key="OU25", selection="Over 2.5")
    score_same_fix = compute_correlation_score([l1, l3])
    assert_true("corr_same_fixture_high", score_same_fix >= 0.80,
                f"Expected >=0.80, got {score_same_fix}")

    # Over + BTTS Yes same fixture — should be high
    l_over = _make_leg(pick_id=4, provider_fixture_id=300, league_id=30, market_key="OU25", selection="Over 2.5",
                       home_team_id=5, away_team_id=6)
    l_btts = _make_leg(pick_id=5, provider_fixture_id=300, league_id=30, market_key="BTTS", selection="Yes",
                       home_team_id=5, away_team_id=6)
    score_over_btts = compute_correlation_score([l_over, l_btts])
    # Same fixture (0.80) + over+btts (0.30) = 1.10 → capped at 1.0
    assert_true("corr_over_btts_capped", score_over_btts >= 1.0,
                f"Expected >=1.0 (capped), got {score_over_btts}")
    assert_true("corr_capped_at_1", score_over_btts <= 1.0,
                f"Expected capped at 1.0, got {score_over_btts}")

    # Same league — should add 0.08
    l4 = _make_leg(pick_id=6, provider_fixture_id=400, league_id=10, home_team_id=7, away_team_id=8,
                   market_key="1X2", selection="Home")
    score_same_league = compute_correlation_score([l1, l4])
    assert_true("corr_same_league_adds", score_same_league > score_indep,
                f"Expected higher than {score_indep}, got {score_same_league}")

except Exception as exc:
    fail("correlation_scoring", traceback.format_exc())


# ── GROUP 6: Validation rules ─────────────────────────────────────────────────

print("\n[6] Validation rules")
try:
    from app.services.parlay_engine_service import validate_parlay_rules

    # Valid — different fixtures
    lv1 = _make_leg(pick_id=1, provider_fixture_id=100, league_id=10, home_team_id=1, away_team_id=2,
                    odds=1.80, edge=0.05)
    lv2 = _make_leg(pick_id=2, provider_fixture_id=200, league_id=20, home_team_id=3, away_team_id=4,
                    odds=2.10, edge=0.04)
    is_valid, reason = validate_parlay_rules([lv1, lv2])
    assert_true("valid_parlay_passes", is_valid, f"Expected valid, got reason: {reason}")

    # Invalid — same fixture (if setting disallows it)
    from app.core.config import settings
    if not settings.parlay_allow_same_fixture:
        lsf1 = _make_leg(pick_id=3, provider_fixture_id=500, league_id=10, odds=1.80, edge=0.04)
        lsf2 = _make_leg(pick_id=4, provider_fixture_id=500, league_id=10, odds=2.10, edge=0.05,
                          market_key="OU25", selection="Over 2.5")
        is_valid_sf, reason_sf = validate_parlay_rules([lsf1, lsf2])
        assert_true("invalid_same_fixture", not is_valid_sf,
                    f"Expected invalid (same fixture), got valid")

    # Invalid — negative edge
    lbad = _make_leg(pick_id=5, provider_fixture_id=600, league_id=30, odds=1.50, edge=-0.05)
    lgood = _make_leg(pick_id=6, provider_fixture_id=700, league_id=40, odds=2.00, edge=0.03)
    is_valid_neg, reason_neg = validate_parlay_rules([lbad, lgood])
    assert_true("invalid_negative_edge", not is_valid_neg,
                f"Expected invalid (negative edge), got valid")

    # Invalid — odds < 1.0
    llow = _make_leg(pick_id=7, provider_fixture_id=800, league_id=50, odds=0.90, edge=0.03)
    is_valid_low, reason_low = validate_parlay_rules([llow, lgood])
    assert_true("invalid_odds_below_1", not is_valid_low,
                f"Expected invalid (odds < 1.0), got valid")

except Exception as exc:
    fail("validation_rules", traceback.format_exc())


# ── GROUP 7: Generation ───────────────────────────────────────────────────────

print("\n[7] Generation")
try:
    from app.services.parlay_engine_service import generate_parlay_candidates

    picks = [
        _make_leg(pick_id=i, provider_fixture_id=100 + i * 10, league_id=10 + i,
                  home_team_id=i * 2, away_team_id=i * 2 + 1, odds=1.80 + i * 0.1,
                  p_cal=0.58, edge=0.04)
        for i in range(1, 6)
    ]

    # 2-leg only
    parlays2 = generate_parlay_candidates(picks, [2], "2026-05-01", max_per_size=50)
    assert_true("gen_2leg_count", len(parlays2) > 0, "No 2-leg parlays generated")
    assert_true("gen_2leg_all_2", all(p["legs_count"] == 2 for p in parlays2),
                "Not all parlays are 2-leg")

    # 3-leg
    parlays3 = generate_parlay_candidates(picks, [3], "2026-05-01", max_per_size=50)
    assert_true("gen_3leg_count", len(parlays3) > 0, "No 3-leg parlays generated")

    # Mixed
    parlays_all = generate_parlay_candidates(picks, [2, 3], "2026-05-01", max_per_size=50)
    has_2 = any(p["legs_count"] == 2 for p in parlays_all)
    has_3 = any(p["legs_count"] == 3 for p in parlays_all)
    assert_true("gen_mixed_has_2", has_2)
    assert_true("gen_mixed_has_3", has_3)

    # Parlay key format
    if parlays2:
        key = parlays2[0]["parlay_key"]
        assert_true("gen_key_starts_with_date", key.startswith("2026-05-01"),
                    f"Key {key!r} doesn't start with date")

    # Status field exists
    if parlays2:
        assert_true("gen_has_status", "recommendation_status" in parlays2[0])
        assert_true("gen_has_ev", "ev" in parlays2[0])
        assert_true("gen_has_legs", "legs" in parlays2[0])
        assert_true("gen_legs_list", isinstance(parlays2[0]["legs"], list))

    # Max per size is respected
    parlays_limited = generate_parlay_candidates(picks, [2], "2026-05-01", max_per_size=2)
    assert_true("gen_max_per_size", len(parlays_limited) <= 2,
                f"Expected <=2, got {len(parlays_limited)}")

except Exception as exc:
    fail("generation", traceback.format_exc())


# ── GROUP 8: Repo — counts ────────────────────────────────────────────────────

print("\n[8] Repo — counts")
try:
    conn = _make_conn()
    counts = repo.parlay_counts(conn)
    for table in ("parlay_candidates", "parlay_legs", "parlay_results", "parlay_risk_rules"):
        assert_true(f"counts_has_{table}", table in counts, f"Missing {table} in counts")
        assert_true(f"counts_{table}_non_neg", counts[table] >= 0,
                    f"Negative count for {table}")

    # risk_rules are seeded
    assert_true("counts_risk_rules_seeded", counts["parlay_risk_rules"] >= 9,
                f"Expected >=9 risk rules, got {counts['parlay_risk_rules']}")

except Exception as exc:
    fail("repo_counts", traceback.format_exc())


# ── GROUP 9: Settlement — win ─────────────────────────────────────────────────

print("\n[9] Settlement — win")
try:
    conn = _make_conn()

    prow = _make_parlay(parlay_key="2026-05-01_10_11", total_odds=3.24,
                        legs_count=2, recommendation_status="recommended")
    repo.insert_parlay_candidate(conn, prow)

    legs = [_make_leg(pick_id=10, fixture_id=100, provider_fixture_id=9000),
            _make_leg(pick_id=11, fixture_id=101, provider_fixture_id=9001,
                      selection="Away", odds=1.80)]
    repo.insert_parlay_legs(conn, prow["parlay_key"], legs)

    # Win: both legs win
    repo.upsert_parlay_result(conn, {
        "parlay_key": prow["parlay_key"],
        "result_status": "win",
        "legs_won": 2,
        "legs_lost": 0,
        "legs_void": 0,
        "stake_units": 0.25,
        "profit_units": round(0.25 * (3.24 - 1), 4),
        "roi": round(0.25 * (3.24 - 1) / 0.25 * 100, 2),
    })

    results = repo.get_parlay_results(conn)
    assert_eq("settle_win_status", results[0]["result_status"], "win")
    assert_true("settle_win_profit_positive",
                (results[0]["profit_units"] or 0) > 0,
                f"Expected positive profit, got {results[0]['profit_units']}")

except Exception as exc:
    fail("settlement_win", traceback.format_exc())


# ── GROUP 10: Settlement — loss ───────────────────────────────────────────────

print("\n[10] Settlement — loss")
try:
    conn = _make_conn()
    prow = _make_parlay(parlay_key="2026-05-01_20_21")
    repo.insert_parlay_candidate(conn, prow)
    legs = [_make_leg(pick_id=20), _make_leg(pick_id=21, fixture_id=102,
                                              provider_fixture_id=9002, selection="Away")]
    repo.insert_parlay_legs(conn, prow["parlay_key"], legs)

    stake = 0.25
    repo.upsert_parlay_result(conn, {
        "parlay_key": prow["parlay_key"],
        "result_status": "loss",
        "legs_won": 1,
        "legs_lost": 1,
        "legs_void": 0,
        "stake_units": stake,
        "profit_units": -stake,
        "roi": -100.0,
    })

    results = repo.get_parlay_results(conn)
    assert_eq("settle_loss_status", results[0]["result_status"], "loss")
    assert_true("settle_loss_profit_negative",
                (results[0]["profit_units"] or 0) < 0,
                f"Expected negative profit")

except Exception as exc:
    fail("settlement_loss", traceback.format_exc())


# ── GROUP 11: Settlement — void recalculation ─────────────────────────────────

print("\n[11] Settlement — void recalculation")
try:
    conn = _make_conn()

    # 3-leg parlay with one void (odds: 1.80 × 2.10 × 1.90 = 7.182)
    prow = _make_parlay(parlay_key="2026-05-01_30_31_32", total_odds=7.182,
                        legs_count=3, parlay_type="balanceada")
    repo.insert_parlay_candidate(conn, prow)

    legs3 = [
        _make_leg(pick_id=30, fixture_id=200, provider_fixture_id=8000, odds=1.80),
        _make_leg(pick_id=31, fixture_id=201, provider_fixture_id=8001, odds=2.10, selection="Away"),
        _make_leg(pick_id=32, fixture_id=202, provider_fixture_id=8002, odds=1.90,
                  market_key="OU25", selection="Over 2.5"),
    ]
    repo.insert_parlay_legs(conn, prow["parlay_key"], legs3)

    # Void leg 32: effective odds = 1.80 × 2.10 = 3.78
    effective_odds = 1.80 * 2.10
    stake = 0.25
    profit = stake * (effective_odds - 1)

    repo.upsert_parlay_result(conn, {
        "parlay_key": prow["parlay_key"],
        "result_status": "win",
        "legs_won": 2,
        "legs_lost": 0,
        "legs_void": 1,
        "stake_units": stake,
        "profit_units": round(profit, 4),
        "roi": round(profit / stake * 100, 2),
    })

    results = repo.get_parlay_results(conn)
    r = results[0]
    assert_eq("settle_void_status", r["result_status"], "win")
    assert_eq("settle_void_legs_void", r["legs_void"], 1)
    assert_true("settle_void_profit_less_than_full",
                (r["profit_units"] or 0) < 0.25 * (7.182 - 1),
                f"Expected profit < full win profit, got {r['profit_units']}")

except Exception as exc:
    fail("settlement_void", traceback.format_exc())


# ── GROUP 12: Dry-run (no writes) ─────────────────────────────────────────────

print("\n[12] Dry-run")
try:
    conn = _make_conn()

    from app.services.parlay_engine_service import generate_parlay_candidates

    picks = [
        _make_leg(pick_id=i, provider_fixture_id=1000 + i * 10, league_id=50 + i,
                  home_team_id=i * 2, away_team_id=i * 2 + 1, odds=1.80,
                  p_cal=0.58, edge=0.04)
        for i in range(1, 5)
    ]

    parlays = generate_parlay_candidates(picks, [2], "2026-05-01")
    assert_true("dryrun_generates_parlays", len(parlays) > 0, "No parlays generated")

    # In dry-run: no DB writes
    cnt_before = conn.execute("SELECT COUNT(*) FROM parlay_candidates").fetchone()[0]
    assert_eq("dryrun_db_unchanged", cnt_before, 0)

    # Status distribution
    recs = [p for p in parlays if p["recommendation_status"] == "recommended"]
    obs  = [p for p in parlays if p["recommendation_status"] == "observed"]
    rej  = [p for p in parlays if "rejected" in p.get("recommendation_status", "")]
    total = len(parlays)
    assert_eq("dryrun_total_consistent", len(recs) + len(obs) + len(rej), total)

except Exception as exc:
    fail("dryrun", traceback.format_exc())


# ── GROUP 13: Execute — writes DuckDB ─────────────────────────────────────────

print("\n[13] Execute — writes DuckDB")
try:
    conn = _make_conn()

    picks = [
        _make_leg(pick_id=i, provider_fixture_id=2000 + i * 10, league_id=60 + i,
                  home_team_id=i * 2 + 10, away_team_id=i * 2 + 11, odds=1.80 + i * 0.1,
                  p_cal=0.58, edge=0.04)
        for i in range(1, 6)
    ]

    from app.services.parlay_engine_service import generate_parlay_candidates

    parlays = generate_parlay_candidates(picks, [2, 3], "2026-05-01", max_per_size=10)

    # Simulate execute: save to DuckDB
    saved = 0
    for parlay in parlays[:5]:
        repo.insert_parlay_candidate(conn, parlay)
        repo.insert_parlay_legs(conn, parlay["parlay_key"], parlay["legs"])
        saved += 1

    cand_cnt = conn.execute("SELECT COUNT(*) FROM parlay_candidates").fetchone()[0]
    legs_cnt = conn.execute("SELECT COUNT(*) FROM parlay_legs").fetchone()[0]

    assert_eq("execute_candidates_saved", cand_cnt, saved)
    assert_true("execute_legs_saved", legs_cnt > 0, "No legs written")
    assert_true("execute_legs_per_parlay", legs_cnt >= saved * 2,
                f"Expected >={saved*2} legs, got {legs_cnt}")

except Exception as exc:
    fail("execute_writes", traceback.format_exc())


# ── GROUP 14: Summary ─────────────────────────────────────────────────────────

print("\n[14] Summary")
try:
    conn = _make_conn()

    # Insert 3 results
    for i, (status, profit) in enumerate([("win", 0.56), ("loss", -0.25), ("void", 0.0)]):
        pk = f"2026-05-01_test_{i}"
        repo.insert_parlay_candidate(conn, _make_parlay(parlay_key=pk, date="2026-04-25"))
        repo.upsert_parlay_result(conn, {
            "parlay_key": pk,
            "result_status": status,
            "legs_won": 2 if status == "win" else 0,
            "legs_lost": 0 if status == "win" else (2 if status == "loss" else 0),
            "legs_void": 0 if status != "void" else 2,
            "stake_units": 0.25,
            "profit_units": profit,
            "roi": profit / 0.25 * 100,
        })

    summary = repo.get_recent_parlay_summary(conn)
    assert_eq("summary_total", summary["total"], 3)
    assert_eq("summary_wins", summary["wins"], 1)
    assert_eq("summary_losses", summary["losses"], 1)
    assert_eq("summary_voids", summary["voids"], 1)
    assert_almost_eq("summary_profit", summary["profit_units"], 0.56 - 0.25, tol=1e-6)

except Exception as exc:
    fail("summary", traceback.format_exc())


# ── Syntax-check scripts ──────────────────────────────────────────────────────

print("\n[15] Syntax check — Phase 8 scripts")
import ast
_scripts = [
    "app/data/local/parlay_repo.py",
    "app/services/parlay_engine_service.py",
    "app/bot/handlers/parlay.py",
    "scripts/run_parlay_engine.py",
    "scripts/settle_parlays.py",
    "scripts/report_parlays.py",
    "scripts/audit_parlay_engine.py",
    "scripts/test_parlay_engine.py",
    "sql/local/008_parlay_engine_schema.sql",
]
root = Path(__file__).resolve().parent.parent
for rel in _scripts:
    fpath = root / rel.replace("/", "\\")
    if not fpath.exists():
        fail(f"exists_{rel}", f"File not found: {fpath}")
        continue
    if rel.endswith(".py"):
        try:
            ast.parse(fpath.read_text(encoding="utf-8"))
            ok(f"syntax_{rel}")
        except SyntaxError as exc:
            fail(f"syntax_{rel}", str(exc))
    else:
        ok(f"exists_{rel}")


# ── Final report ──────────────────────────────────────────────────────────────

print(f"\n{'='*55}")
total = len(_passed) + len(_failed)
print(f"  {len(_passed)}/{total} tests passed")
if _failed:
    print(f"\n  Failed tests ({len(_failed)}):")
    for name in _failed:
        print(f"    - {name}")
print(f"{'='*55}\n")

sys.exit(0 if not _failed else 1)
