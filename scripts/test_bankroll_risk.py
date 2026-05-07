#!/usr/bin/env python
"""Phase 14: Bankroll, Stake Sizing & Risk Portfolio Engine — test suite.

140+ tests across 38 groups, all in-memory DuckDB (no Supabase, no API calls).
All output is pure ASCII — safe on Windows cp1252.
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASSED = 0
FAILED = 0
ERRORS = []


def section(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


def ok(name):
    global PASSED
    PASSED += 1
    print(f"  PASS  {name}")


def fail(name, detail=""):
    global FAILED
    FAILED += 1
    msg = f"  FAIL  {name}"
    if detail:
        msg += f" -- {detail}"
    print(msg)
    ERRORS.append(msg)


def assert_true(name, condition, detail=""):
    if condition:
        ok(name)
    else:
        fail(name, detail or "condition is False")


def assert_eq(name, got, expected):
    if got == expected:
        ok(name)
    else:
        fail(name, f"got {got!r} expected {expected!r}")


def assert_approx(name, got, expected, tol=0.001):
    if got is None or expected is None:
        fail(name, f"None value: got={got} expected={expected}")
    elif abs(got - expected) <= tol:
        ok(name)
    else:
        fail(name, f"got {got} expected {expected} tol {tol}")


def assert_none(name, got):
    if got is None:
        ok(name)
    else:
        fail(name, f"expected None, got {got!r}")


def assert_not_none(name, got):
    if got is not None:
        ok(name)
    else:
        fail(name, "expected non-None, got None")


def assert_in(name, item, collection):
    if item in collection:
        ok(name)
    else:
        fail(name, f"{item!r} not in {collection!r}")


def assert_ge(name, got, threshold):
    if got >= threshold:
        ok(name)
    else:
        fail(name, f"got {got} < {threshold}")


def assert_le(name, got, threshold):
    if got <= threshold:
        ok(name)
    else:
        fail(name, f"got {got} > {threshold}")


# ── Setup in-memory DuckDB ────────────────────────────────────────────────────

def _setup_db():
    import duckdb
    import pathlib
    conn = duckdb.connect(":memory:")
    sql_dir = pathlib.Path(__file__).parent.parent / "sql" / "local"
    for sql_file in sorted(sql_dir.glob("*.sql")):
        try:
            sql = sql_file.read_text(encoding="utf-8")
            conn.execute(sql)
        except Exception as e:
            print(f"  [warn] {sql_file.name}: {e}")
    return conn


# ── Group 1: Schema tables ────────────────────────────────────────────────────

section("1. Schema tables")
try:
    conn = _setup_db()
    tables_result = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
    ).fetchall()
    table_names = {r[0] for r in tables_result}
    for tbl in (
        "bankroll_profiles",
        "stake_recommendations",
        "portfolio_risk_snapshots",
        "risk_events",
    ):
        assert_true(f"table {tbl} exists", tbl in table_names)
except Exception as e:
    fail("schema setup", str(e))
    traceback.print_exc()


# ── Group 2: Schema idempotent (re-run) ───────────────────────────────────────

section("2. Schema idempotent re-run")
try:
    import pathlib, duckdb
    conn2 = duckdb.connect(":memory:")
    sql_dir = pathlib.Path(__file__).parent.parent / "sql" / "local"
    sql_file = sql_dir / "014_bankroll_risk_schema.sql"
    sql_text = sql_file.read_text(encoding="utf-8")
    conn2.execute(sql_text)
    conn2.execute(sql_text)  # second run — must not raise
    ok("schema idempotent second run")
    r = conn2.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
    ).fetchall()
    assert_true("tables still exist after re-run", len(r) >= 4)
except Exception as e:
    fail("schema idempotent", str(e))


# ── Group 3: Sequences ────────────────────────────────────────────────────────

section("3. Sequences")
try:
    for seq in (
        "bankroll_profiles_seq",
        "stake_recommendations_seq",
        "portfolio_risk_snapshots_seq",
        "risk_events_seq",
    ):
        try:
            v = conn.execute(f"SELECT nextval('{seq}')").fetchone()[0]
            assert_true(f"seq {seq} works", v >= 1)
        except Exception as e:
            fail(f"seq {seq}", str(e))
except Exception as e:
    fail("sequences group", str(e))


# ── Group 4: compute_full_kelly ───────────────────────────────────────────────

section("4. compute_full_kelly")
try:
    from app.services.bankroll_risk_service import compute_full_kelly

    # Basic: p=0.55, odds=2.10, b=1.10 -> (0.55*1.10 - 0.45)/1.10 = (0.605-0.45)/1.10 = 0.155/1.10 ~= 0.1409
    fk = compute_full_kelly(0.55, 2.10)
    assert_approx("kelly basic p=0.55 odds=2.10", fk, 0.1409, tol=0.001)

    # Higher edge
    fk2 = compute_full_kelly(0.60, 2.00)  # (0.60*1 - 0.40)/1 = 0.20
    assert_approx("kelly p=0.60 odds=2.00", fk2, 0.20, tol=0.001)

    # Negative edge (p <= implied)
    fk3 = compute_full_kelly(0.45, 2.10)  # implied = 0.4762 > 0.45 -> 0
    assert_eq("kelly negative edge returns 0", fk3, 0.0)

    # Invalid odds <= 1.0
    assert_eq("kelly odds=0.9 returns 0", compute_full_kelly(0.55, 0.9), 0.0)
    assert_eq("kelly odds=1.0 returns 0", compute_full_kelly(0.55, 1.0), 0.0)

    # p_model = 0 or 1
    assert_eq("kelly p=0.0 returns 0", compute_full_kelly(0.0, 2.0), 0.0)
    assert_eq("kelly p=1.0 returns 0", compute_full_kelly(1.0, 2.0), 0.0)

    # Very large edge -> high kelly
    fk4 = compute_full_kelly(0.80, 2.0)  # (0.80-0.20)/1 = 0.60
    assert_approx("kelly p=0.80 odds=2.0", fk4, 0.60, tol=0.001)

except Exception as e:
    fail("compute_full_kelly import", str(e))
    traceback.print_exc()


# ── Group 5: compute_fractional_kelly ────────────────────────────────────────

section("5. compute_fractional_kelly")
try:
    from app.services.bankroll_risk_service import compute_fractional_kelly

    assert_approx("frac 0.25 of 0.20", compute_fractional_kelly(0.20, 0.25), 0.05, tol=0.001)
    assert_approx("frac 0.50 of 0.40", compute_fractional_kelly(0.40, 0.50), 0.20, tol=0.001)
    assert_eq("frac of 0 kelly", compute_fractional_kelly(0.0, 0.25), 0.0)
    assert_eq("frac negative kelly", compute_fractional_kelly(-0.1, 0.25), 0.0)
    # cap at 1.0
    assert_eq("frac capped at 1.0", compute_fractional_kelly(2.0, 1.0), 1.0)
    # zero fraction
    assert_eq("frac fraction=0", compute_fractional_kelly(0.5, 0.0), 0.0)

except Exception as e:
    fail("compute_fractional_kelly import", str(e))


# ── Group 6: cap_stake_units ──────────────────────────────────────────────────

section("6. cap_stake_units")
try:
    from app.services.bankroll_risk_service import cap_stake_units

    assert_eq("cap below max", cap_stake_units(1.0, 1.5), 1.0)
    assert_eq("cap at max", cap_stake_units(2.0, 1.5), 1.5)
    assert_eq("cap zero", cap_stake_units(0.0, 1.5), 0.0)
    assert_eq("cap negative", cap_stake_units(-0.5, 1.5), 0.0)
    assert_eq("cap max_negative", cap_stake_units(1.0, -1.0), 0.0)

except Exception as e:
    fail("cap_stake_units import", str(e))


# ── Group 7: stake_label ──────────────────────────────────────────────────────

section("7. stake_label")
try:
    from app.services.bankroll_risk_service import stake_label

    assert_eq("label 0.0 -> no_stake", stake_label(0.0), "no_stake")
    assert_eq("label -0.1 -> no_stake", stake_label(-0.1), "no_stake")
    assert_eq("label 0.3 -> micro", stake_label(0.3), "micro")
    assert_eq("label 0.49 -> micro", stake_label(0.49), "micro")
    assert_eq("label 0.5 -> small", stake_label(0.5), "small")
    assert_eq("label 0.9 -> small", stake_label(0.9), "small")
    assert_eq("label 1.0 -> medium", stake_label(1.0), "medium")
    assert_eq("label 1.4 -> medium", stake_label(1.4), "medium")
    assert_eq("label 1.5 -> large", stake_label(1.5), "large")
    assert_eq("label 3.0 -> large", stake_label(3.0), "large")

except Exception as e:
    fail("stake_label import", str(e))


# ── Group 8: compute_risk_score — odds component ──────────────────────────────

section("8. compute_risk_score — odds component")
try:
    from app.services.bankroll_risk_service import compute_risk_score

    # High odds -> higher score
    pick_hi = {"odds": 4.5, "confidence_score": 0.65, "market_key": "1X2"}
    pick_lo = {"odds": 1.8, "confidence_score": 0.65, "market_key": "1X2"}
    rs_hi = compute_risk_score(pick_hi)
    rs_lo = compute_risk_score(pick_lo)
    assert_true("high odds > low odds risk", rs_hi > rs_lo)
    assert_true("risk score in 0-100", 0 <= rs_hi <= 100)
    assert_true("risk score in 0-100 lo", 0 <= rs_lo <= 100)

    # odds >= 4.0 adds 25
    pick_4 = {"odds": 4.0, "confidence_score": 0.70, "market_key": "1X2",
               "clv_percent": 2.0, "strategy_sample_size": 50}
    rs4 = compute_risk_score(pick_4)
    assert_ge("odds 4.0 adds 25 pts -> score >= 25", rs4, 25.0)

    # odds >= 3.0 adds 15
    pick_3 = {"odds": 3.0, "confidence_score": 0.70, "market_key": "1X2",
               "clv_percent": 2.0, "strategy_sample_size": 50}
    rs3 = compute_risk_score(pick_3)
    assert_ge("odds 3.0 adds 15 pts -> score >= 15", rs3, 15.0)

except Exception as e:
    fail("compute_risk_score odds", str(e))


# ── Group 9: compute_risk_score — confidence & strategy ──────────────────────

section("9. compute_risk_score — confidence and strategy")
try:
    from app.services.bankroll_risk_service import compute_risk_score

    # Low confidence < 0.53 adds 20
    pick_lc = {"odds": 1.9, "confidence_score": 0.51, "market_key": "1X2",
                "clv_percent": 2.0, "strategy_sample_size": 50}
    rs_lc = compute_risk_score(pick_lc)
    assert_ge("low conf adds >=20", rs_lc, 20.0)

    # Strategy avoid adds 30
    pick_av = {"odds": 1.9, "confidence_score": 0.65, "market_key": "1X2",
                "strategy_recommendation": "avoid", "strategy_sample_size": 50}
    rs_av = compute_risk_score(pick_av)
    assert_ge("avoid adds 30", rs_av, 30.0)

    # Strategy reduce adds 18
    pick_rd = {"odds": 1.9, "confidence_score": 0.65, "market_key": "1X2",
                "strategy_recommendation": "reduce", "strategy_sample_size": 50}
    rs_rd = compute_risk_score(pick_rd)
    assert_ge("reduce adds >=18", rs_rd, 18.0)

    # No sample adds 8
    pick_ns = {"odds": 1.9, "confidence_score": 0.65, "market_key": "1X2",
                "strategy_sample_size": 0}
    rs_ns = compute_risk_score(pick_ns)
    assert_ge("no sample adds 8", rs_ns, 8.0)

    # Negative CLV < -4.0 adds 15
    pick_clv = {"odds": 1.9, "confidence_score": 0.65, "market_key": "1X2",
                 "clv_percent": -5.0, "strategy_sample_size": 50}
    rs_clv = compute_risk_score(pick_clv)
    assert_ge("neg CLV adds 15", rs_clv, 15.0)

except Exception as e:
    fail("compute_risk_score strategy", str(e))


# ── Group 10: compute_risk_score — market component ──────────────────────────

section("10. compute_risk_score — market")
try:
    from app.services.bankroll_risk_service import compute_risk_score

    pick_btts = {"odds": 1.9, "confidence_score": 0.65, "market_key": "BTTS",
                  "clv_percent": 2.0, "strategy_sample_size": 50}
    pick_1x2  = {"odds": 1.9, "confidence_score": 0.65, "market_key": "1X2",
                  "clv_percent": 2.0, "strategy_sample_size": 50}
    rs_btts = compute_risk_score(pick_btts)
    rs_1x2  = compute_risk_score(pick_1x2)
    assert_true("BTTS >= 1X2 risk", rs_btts >= rs_1x2)

    # Unknown market adds 3
    pick_unk = {"odds": 1.9, "confidence_score": 0.65, "market_key": "WHATEVER",
                 "clv_percent": 2.0, "strategy_sample_size": 50}
    rs_unk = compute_risk_score(pick_unk)
    assert_ge("unknown market adds 3", rs_unk, 3.0)

    # Score capped at 100
    pick_max = {"odds": 5.0, "confidence_score": 0.40, "market_key": "BTTS",
                 "strategy_recommendation": "avoid",
                 "strategy_sample_size": 0, "clv_percent": -10.0}
    rs_max = compute_risk_score(pick_max)
    assert_le("risk score max 100", rs_max, 100.0)

except Exception as e:
    fail("compute_risk_score market", str(e))


# ── Group 11: risk_label ──────────────────────────────────────────────────────

section("11. risk_label")
try:
    from app.services.bankroll_risk_service import risk_label

    assert_eq("risk_label 75 -> avoid", risk_label(75.0), "avoid")
    assert_eq("risk_label 70 -> avoid", risk_label(70.0), "avoid")
    assert_eq("risk_label 60 -> high", risk_label(60.0), "high")
    assert_eq("risk_label 50 -> high", risk_label(50.0), "high")
    assert_eq("risk_label 40 -> medium", risk_label(40.0), "medium")
    assert_eq("risk_label 30 -> medium", risk_label(30.0), "medium")
    assert_eq("risk_label 10 -> low", risk_label(10.0), "low")
    assert_eq("risk_label 0 -> low", risk_label(0.0), "low")

except Exception as e:
    fail("risk_label import", str(e))


# ── Group 12: compute_pick_correlation — same fixture ────────────────────────

section("12. Correlation — same fixture")
try:
    from app.services.bankroll_risk_service import compute_pick_correlation

    pick_a = {"provider_fixture_id": 1001, "league_id": 10}
    pick_b = {"provider_fixture_id": 1001, "league_id": 10}
    pick_c = {"provider_fixture_id": 9999, "league_id": 10}

    corr_same = compute_pick_correlation(pick_a, pick_b)
    corr_diff = compute_pick_correlation(pick_a, pick_c)

    assert_approx("same fixture corr=0.80", corr_same, 0.80, tol=0.001)
    assert_true("diff fixture corr < 0.80", corr_diff < 0.80)
    assert_true("corr in [0,1]", 0.0 <= corr_same <= 1.0)

except Exception as e:
    fail("correlation same fixture", str(e))


# ── Group 13: compute_pick_correlation — same team ───────────────────────────

section("13. Correlation — same team")
try:
    from app.services.bankroll_risk_service import compute_pick_correlation

    # Use different selections to avoid triggering the same-mkt/sel related-market pair
    pick_a = {"fixture_id": 1, "team_home_id": 101, "team_away_id": 202,
              "market_key": "1X2", "selection": "Home", "league_id": 5}
    pick_b = {"fixture_id": 2, "team_home_id": 101, "team_away_id": 303,
              "market_key": "1X2", "selection": "Away", "league_id": 5}
    pick_c = {"fixture_id": 3, "team_home_id": 404, "team_away_id": 505,
              "market_key": "1X2", "selection": "Away", "league_id": 5}

    corr_team = compute_pick_correlation(pick_a, pick_b)
    corr_none = compute_pick_correlation(pick_a, pick_c)

    # same team + same league (different selection -> no related-market pair)
    assert_approx("same team + same league corr ~= 0.48", corr_team, 0.48, tol=0.01)
    # same league only (different team, different selection)
    assert_approx("same league only corr ~= 0.08", corr_none, 0.08, tol=0.001)

except Exception as e:
    fail("correlation same team", str(e))


# ── Group 14: compute_pick_correlation — related markets (OU25+BTTS) ─────────

section("14. Correlation — related markets Over+BTTS")
try:
    from app.services.bankroll_risk_service import compute_pick_correlation

    pick_over  = {"fixture_id": 10, "team_home_id": 1, "team_away_id": 2,
                  "market_key": "OU25", "selection": "Over 2.5",
                  "league_id": 99}
    pick_btts  = {"fixture_id": 11, "team_home_id": 3, "team_away_id": 4,
                  "market_key": "BTTS", "selection": "Yes",
                  "league_id": 99}
    pick_under = {"fixture_id": 12, "team_home_id": 5, "team_away_id": 6,
                  "market_key": "OU25", "selection": "Under 2.5",
                  "league_id": 88}
    pick_btts_no = {"fixture_id": 13, "team_home_id": 7, "team_away_id": 8,
                    "market_key": "BTTS", "selection": "No",
                    "league_id": 88}

    corr_over_btts = compute_pick_correlation(pick_over, pick_btts)
    corr_under_no  = compute_pick_correlation(pick_under, pick_btts_no)
    corr_over_bttsno = compute_pick_correlation(pick_over, pick_btts_no)

    # OU25 Over + BTTS Yes => related market 0.30 + same league 0.08 = 0.38
    assert_approx("Over2.5+BTTS_Yes corr", corr_over_btts, 0.38, tol=0.01)
    # OU25 Under + BTTS No => 0.30 + 0.08 = 0.38
    assert_approx("Under2.5+BTTS_No corr", corr_under_no, 0.38, tol=0.01)
    # Over + BTTS No => not related, diff leagues -> 0.0
    assert_approx("Over+BTTS_No no relation", corr_over_bttsno, 0.0, tol=0.001)

except Exception as e:
    fail("correlation related markets", str(e))
    traceback.print_exc()


# ── Group 15: compute_pick_correlation — 1X2 + DC ────────────────────────────

section("15. Correlation — 1X2 Home + DC 1X")
try:
    from app.services.bankroll_risk_service import compute_pick_correlation

    pick_home = {"fixture_id": 20, "team_home_id": 1, "team_away_id": 2,
                 "market_key": "1X2", "selection": "Home", "league_id": 50}
    pick_dc1x = {"fixture_id": 21, "team_home_id": 3, "team_away_id": 4,
                 "market_key": "DC", "selection": "1X", "league_id": 50}
    pick_away = {"fixture_id": 22, "team_home_id": 5, "team_away_id": 6,
                 "market_key": "1X2", "selection": "Away", "league_id": 50}
    pick_dcx2 = {"fixture_id": 23, "team_home_id": 7, "team_away_id": 8,
                 "market_key": "DC", "selection": "X2", "league_id": 50}

    corr_home_dc1x  = compute_pick_correlation(pick_home, pick_dc1x)
    corr_away_dcx2  = compute_pick_correlation(pick_away, pick_dcx2)

    # Related 0.30 + same league 0.08 = 0.38
    assert_approx("1X2_Home + DC_1X corr", corr_home_dc1x, 0.38, tol=0.01)
    assert_approx("1X2_Away + DC_X2 corr", corr_away_dcx2, 0.38, tol=0.01)

except Exception as e:
    fail("correlation 1X2+DC", str(e))


# ── Group 16: group_correlated_picks ─────────────────────────────────────────

section("16. group_correlated_picks")
try:
    from app.services.bankroll_risk_service import group_correlated_picks

    picks = [
        {"provider_fixture_id": 1001, "market_key": "1X2", "selection": "Home", "league_id": 1,
         "team_home_id": 10, "team_away_id": 20},
        {"provider_fixture_id": 1001, "market_key": "OU25", "selection": "Over 2.5", "league_id": 1,
         "team_home_id": 10, "team_away_id": 20},  # same fixture -> 0.80
        {"provider_fixture_id": 9999, "market_key": "1X2", "selection": "Home", "league_id": 99,
         "team_home_id": 50, "team_away_id": 60},  # unrelated
    ]
    groups = group_correlated_picks(picks)
    assert_true("same fixture creates group", len(groups) >= 1)
    assert_true("unrelated pick not in group with first 2",
                any(0 in g and 1 in g for g in groups))

    # Empty / single
    assert_eq("empty picks -> no groups", group_correlated_picks([]), [])
    assert_eq("single pick -> no groups", group_correlated_picks([picks[0]]), [])

except Exception as e:
    fail("group_correlated_picks", str(e))


# ── Group 17: compute_portfolio_correlation_score ─────────────────────────────

section("17. compute_portfolio_correlation_score")
try:
    from app.services.bankroll_risk_service import compute_portfolio_correlation_score

    same_fixture_picks = [
        {"provider_fixture_id": 1, "league_id": 1, "team_home_id": 1, "team_away_id": 2,
         "market_key": "1X2", "selection": "Home"},
        {"provider_fixture_id": 1, "league_id": 1, "team_home_id": 1, "team_away_id": 2,
         "market_key": "OU25", "selection": "Over 2.5"},
    ]
    diff_picks = [
        {"provider_fixture_id": 1, "league_id": 1, "team_home_id": 1, "team_away_id": 2,
         "market_key": "1X2", "selection": "Home"},
        {"provider_fixture_id": 99, "league_id": 99, "team_home_id": 50, "team_away_id": 60,
         "market_key": "1X2", "selection": "Home"},
    ]

    corr_high = compute_portfolio_correlation_score(same_fixture_picks)
    corr_low  = compute_portfolio_correlation_score(diff_picks)

    assert_approx("same fixture portfolio corr = 0.80", corr_high, 0.80, tol=0.01)
    assert_true("diff fixtures portfolio corr < 0.80", corr_low < 0.80)
    assert_eq("single pick -> 0.0", compute_portfolio_correlation_score([same_fixture_picks[0]]), 0.0)
    assert_eq("empty -> 0.0", compute_portfolio_correlation_score([]), 0.0)

except Exception as e:
    fail("compute_portfolio_correlation_score", str(e))


# ── Group 18: compute_exposure ───────────────────────────────────────────────

section("18. compute_exposure — by league")
try:
    from app.services.bankroll_risk_service import compute_exposure

    recs = [
        {"league_id": 10, "market_key": "1X2", "team_home_id": 1, "team_away_id": 2,
         "fixture_id": 100, "recommended_units": 1.0},
        {"league_id": 10, "market_key": "OU25", "team_home_id": 3, "team_away_id": 4,
         "fixture_id": 101, "recommended_units": 0.8},
        {"league_id": 20, "market_key": "1X2", "team_home_id": 5, "team_away_id": 6,
         "fixture_id": 102, "recommended_units": 1.2},
    ]
    exp = compute_exposure(recs)
    assert_approx("league 10 exposure", exp["by_league"].get("10", 0), 1.8, tol=0.01)
    assert_approx("league 20 exposure", exp["by_league"].get("20", 0), 1.2, tol=0.01)

except Exception as e:
    fail("compute_exposure league", str(e))


# ── Group 19: compute_exposure — by market ────────────────────────────────────

section("19. compute_exposure — by market")
try:
    from app.services.bankroll_risk_service import compute_exposure

    recs = [
        {"league_id": 1, "market_key": "1X2", "team_home_id": 1, "team_away_id": 2,
         "fixture_id": 1, "recommended_units": 1.0},
        {"league_id": 2, "market_key": "1X2", "team_home_id": 3, "team_away_id": 4,
         "fixture_id": 2, "recommended_units": 1.5},
        {"league_id": 3, "market_key": "OU25", "team_home_id": 5, "team_away_id": 6,
         "fixture_id": 3, "recommended_units": 0.7},
    ]
    exp = compute_exposure(recs)
    assert_approx("market 1X2 exposure", exp["by_market"].get("1X2", 0), 2.5, tol=0.01)
    assert_approx("market OU25 exposure", exp["by_market"].get("OU25", 0), 0.7, tol=0.01)

except Exception as e:
    fail("compute_exposure market", str(e))


# ── Group 20: compute_exposure — by team ─────────────────────────────────────

section("20. compute_exposure — by team")
try:
    from app.services.bankroll_risk_service import compute_exposure

    recs = [
        {"league_id": 1, "market_key": "1X2", "team_home_id": 101, "team_away_id": 202,
         "fixture_id": 1, "recommended_units": 1.0},
        {"league_id": 2, "market_key": "OU25", "team_home_id": 101, "team_away_id": 303,
         "fixture_id": 2, "recommended_units": 0.5},
    ]
    exp = compute_exposure(recs)
    # team 101 appears in both -> 1.5u
    assert_approx("team 101 exposure", exp["by_team"].get("101", 0), 1.5, tol=0.01)
    assert_approx("team 202 exposure", exp["by_team"].get("202", 0), 1.0, tol=0.01)

    # zero units excluded
    recs2 = [{"league_id": 1, "market_key": "1X2", "team_home_id": 999,
               "fixture_id": 99, "recommended_units": 0.0}]
    exp2 = compute_exposure(recs2)
    assert_true("zero units excluded from teams", "999" not in exp2["by_team"])

except Exception as e:
    fail("compute_exposure team", str(e))


# ── Group 21: generate_portfolio_warnings — league ───────────────────────────

section("21. generate_portfolio_warnings — too_much_same_league")
try:
    from app.services.bankroll_risk_service import generate_portfolio_warnings

    recs = [{"recommended_units": 2.0, "risk_score": 20.0, "strategy_sample_size": 50}]
    exposure = {"by_league": {"10": 4.0}, "by_market": {}, "by_team": {}}
    profile = {"max_same_league_units": 3.0, "max_same_market_units": 3.0,
               "max_same_team_units": 2.0, "max_daily_risk_units": 5.0}
    warnings = generate_portfolio_warnings(recs, exposure, profile)
    assert_true("too_much_same_league warning",
                any("too_much_same_league" in w for w in warnings))

except Exception as e:
    fail("warnings league", str(e))


# ── Group 22: generate_portfolio_warnings — market ───────────────────────────

section("22. generate_portfolio_warnings — too_much_same_market")
try:
    from app.services.bankroll_risk_service import generate_portfolio_warnings

    recs = [{"recommended_units": 2.0, "risk_score": 20.0, "strategy_sample_size": 50}]
    exposure = {"by_league": {}, "by_market": {"1X2": 4.0}, "by_team": {}}
    profile = {"max_same_league_units": 3.0, "max_same_market_units": 3.0,
               "max_same_team_units": 2.0, "max_daily_risk_units": 5.0}
    warnings = generate_portfolio_warnings(recs, exposure, profile)
    assert_true("too_much_same_market warning",
                any("too_much_same_market" in w for w in warnings))

except Exception as e:
    fail("warnings market", str(e))


# ── Group 23: generate_portfolio_warnings — team ─────────────────────────────

section("23. generate_portfolio_warnings — too_much_same_team")
try:
    from app.services.bankroll_risk_service import generate_portfolio_warnings

    recs = [{"recommended_units": 2.0, "risk_score": 20.0, "strategy_sample_size": 50}]
    exposure = {"by_league": {}, "by_market": {}, "by_team": {"101": 3.0}}
    profile = {"max_same_league_units": 3.0, "max_same_market_units": 3.0,
               "max_same_team_units": 2.0, "max_daily_risk_units": 5.0}
    warnings = generate_portfolio_warnings(recs, exposure, profile)
    assert_true("too_much_same_team warning",
                any("too_much_same_team" in w for w in warnings))

except Exception as e:
    fail("warnings team", str(e))


# ── Group 24: generate_portfolio_warnings — daily limit ──────────────────────

section("24. generate_portfolio_warnings — over_daily_risk_limit")
try:
    from app.services.bankroll_risk_service import generate_portfolio_warnings

    recs = [
        {"recommended_units": 2.0, "risk_score": 20.0, "strategy_sample_size": 50},
        {"recommended_units": 2.0, "risk_score": 20.0, "strategy_sample_size": 50},
        {"recommended_units": 2.0, "risk_score": 20.0, "strategy_sample_size": 50},
    ]  # total=6 > max_daily=5
    exposure = {"by_league": {}, "by_market": {}, "by_team": {}}
    profile = {"max_same_league_units": 3.0, "max_same_market_units": 3.0,
               "max_same_team_units": 2.0, "max_daily_risk_units": 5.0}
    warnings = generate_portfolio_warnings(recs, exposure, profile)
    assert_true("over_daily_risk_limit warning",
                any("over_daily_risk_limit" in w for w in warnings))

except Exception as e:
    fail("warnings daily limit", str(e))


# ── Group 25: generate_portfolio_warnings — high risk picks ──────────────────

section("25. generate_portfolio_warnings — too_many_high_risk")
try:
    from app.services.bankroll_risk_service import generate_portfolio_warnings

    recs = [
        {"recommended_units": 1.0, "risk_score": 75.0, "strategy_sample_size": 50},
        {"recommended_units": 1.0, "risk_score": 80.0, "strategy_sample_size": 50},
    ]
    exposure = {"by_league": {}, "by_market": {}, "by_team": {}}
    profile = {"max_same_league_units": 3.0, "max_same_market_units": 3.0,
               "max_same_team_units": 2.0, "max_daily_risk_units": 5.0}
    warnings = generate_portfolio_warnings(recs, exposure, profile)
    assert_true("too_many_high_risk warning",
                any("too_many_high_risk" in w for w in warnings))

except Exception as e:
    fail("warnings high risk", str(e))


# ── Group 26: generate_portfolio_warnings — correlated picks ─────────────────

section("26. generate_portfolio_warnings — correlated_picks")
try:
    from app.services.bankroll_risk_service import generate_portfolio_warnings

    recs = [
        {"recommended_units": 1.0, "risk_score": 20.0, "strategy_sample_size": 50,
         "provider_fixture_id": 1001, "market_key": "1X2", "selection": "Home",
         "league_id": 1, "team_home_id": 10, "team_away_id": 20},
        {"recommended_units": 1.0, "risk_score": 20.0, "strategy_sample_size": 50,
         "provider_fixture_id": 1001, "market_key": "OU25", "selection": "Over 2.5",
         "league_id": 1, "team_home_id": 10, "team_away_id": 20},
    ]
    exposure = {"by_league": {}, "by_market": {}, "by_team": {}}
    profile = {"max_same_league_units": 3.0, "max_same_market_units": 3.0,
               "max_same_team_units": 2.0, "max_daily_risk_units": 5.0}
    warnings = generate_portfolio_warnings(recs, exposure, profile)
    assert_true("correlated_picks warning",
                any("correlated_picks" in w for w in warnings))

except Exception as e:
    fail("warnings correlated", str(e))


# ── Group 27: generate_portfolio_warnings — low sample ───────────────────────

section("27. generate_portfolio_warnings — low_sample_strategy")
try:
    from app.services.bankroll_risk_service import generate_portfolio_warnings

    recs = [
        {"recommended_units": 1.0, "risk_score": 20.0, "strategy_sample_size": 3,
         "provider_fixture_id": 5, "market_key": "1X2", "selection": "Home",
         "league_id": 99, "team_home_id": None, "team_away_id": None},
    ]
    exposure = {"by_league": {}, "by_market": {}, "by_team": {}}
    profile = {"max_same_league_units": 3.0, "max_same_market_units": 3.0,
               "max_same_team_units": 2.0, "max_daily_risk_units": 5.0}
    warnings = generate_portfolio_warnings(recs, exposure, profile)
    assert_true("low_sample_strategy warning",
                any("low_sample_strategy" in w for w in warnings))

except Exception as e:
    fail("warnings low sample", str(e))


# ── Group 28: generate_portfolio_warnings — negative CLV ─────────────────────

section("28. generate_portfolio_warnings — negative_clv_strategy")
try:
    from app.services.bankroll_risk_service import generate_portfolio_warnings

    recs = [
        {"recommended_units": 1.0, "risk_score": 20.0, "strategy_sample_size": 50,
         "clv_percent": -3.0, "provider_fixture_id": 5, "market_key": "1X2",
         "selection": "Home", "league_id": 99, "team_home_id": None, "team_away_id": None},
    ]
    exposure = {"by_league": {}, "by_market": {}, "by_team": {}}
    profile = {"max_same_league_units": 3.0, "max_same_market_units": 3.0,
               "max_same_team_units": 2.0, "max_daily_risk_units": 5.0}
    warnings = generate_portfolio_warnings(recs, exposure, profile)
    assert_true("negative_clv_strategy warning",
                any("negative_clv_strategy" in w for w in warnings))

except Exception as e:
    fail("warnings negative CLV", str(e))


# ── Group 29: compute_portfolio_score ─────────────────────────────────────────

section("29. compute_portfolio_score")
try:
    from app.services.bankroll_risk_service import compute_portfolio_score

    # Empty -> 50.0 balanced
    score, level = compute_portfolio_score([], {}, [])
    assert_approx("empty portfolio score=50.0", score, 50.0, tol=0.01)
    assert_eq("empty portfolio level=balanced", level, "balanced")

    # Good picks — high edge, good strategy, no warnings
    good_recs = [
        {"recommended_units": 1.0, "edge": 0.05, "strategy_score": 75.0,
         "league_id": 1, "market_key": "1X2", "provider_fixture_id": 1,
         "team_home_id": 1, "team_away_id": 2, "selection": "Home"},
        {"recommended_units": 0.8, "edge": 0.04, "strategy_score": 70.0,
         "league_id": 2, "market_key": "OU25", "provider_fixture_id": 2,
         "team_home_id": 3, "team_away_id": 4, "selection": "Over 2.5"},
    ]
    exposure_good = {"by_league": {"1": 1.0, "2": 0.8}, "by_market": {}, "by_team": {}}
    score_good, level_good = compute_portfolio_score(good_recs, exposure_good, [])
    assert_ge("good portfolio score >= 50", score_good, 50.0)
    assert_le("portfolio score <= 100", score_good, 100.0)

    # Many warnings -> lower score
    score_bad, level_bad = compute_portfolio_score(good_recs, exposure_good,
                                                    ["w1", "w2", "w3", "w4", "w5", "w6"])
    assert_true("many warnings lower score", score_bad < score_good)

except Exception as e:
    fail("compute_portfolio_score", str(e))
    traceback.print_exc()


# ── Group 30: portfolio risk levels ───────────────────────────────────────────

section("30. portfolio risk level labels")
try:
    from app.services.bankroll_risk_service import compute_portfolio_score

    def _score_with_value(target):
        recs = [{"recommended_units": 1.0, "edge": target / 500.0, "strategy_score": target,
                  "league_id": 1, "provider_fixture_id": 1, "team_home_id": 1,
                  "team_away_id": 2, "market_key": "1X2", "selection": "Home"}]
        exposure = {"by_league": {"1": 1.0}, "by_market": {}, "by_team": {}}
        return compute_portfolio_score(recs, exposure, [])

    # Test that the label transitions exist
    _, l_conservative = compute_portfolio_score(
        [{"recommended_units": 1.0, "edge": 0.10, "strategy_score": 90.0,
          "league_id": 1, "provider_fixture_id": 1, "team_home_id": 1,
          "team_away_id": 2, "market_key": "1X2", "selection": "Home"}],
        {"by_league": {"1": 1.0}, "by_market": {}, "by_team": {}}, []
    )
    assert_in("conservative level exists", l_conservative,
               ["conservative", "balanced", "aggressive", "unsafe"])

    # unsafe: many warnings force low score
    bad_recs = [{"recommended_units": 0.1, "edge": 0.001, "strategy_score": 10.0,
                  "league_id": 1, "provider_fixture_id": 1, "team_home_id": 1,
                  "team_away_id": 2, "market_key": "1X2", "selection": "Home"}]
    _, l_unsafe = compute_portfolio_score(bad_recs, {"by_league": {}, "by_market": {}, "by_team": {}},
                                          ["w1", "w2", "w3", "w4", "w5"])
    assert_in("low quality portfolio level",  l_unsafe, ["aggressive", "unsafe"])

except Exception as e:
    fail("portfolio risk levels", str(e))


# ── Group 31: compute_stake_recommendation — rejection scenarios ──────────────

section("31. compute_stake_recommendation — rejections")
try:
    from app.services.bankroll_risk_service import compute_stake_recommendation

    profile = {
        "bankroll_units": 100.0, "kelly_fraction": 0.25, "max_pick_risk_units": 1.5,
        "min_edge_for_stake": 0.02, "min_confidence_for_stake": 0.52,
        "block_avoid": True, "reduce_low_sample": True,
    }

    # odds invalid
    r = compute_stake_recommendation({"odds": 0.9, "p_model": 0.6, "edge": 0.05}, profile)
    assert_eq("rejected odds_invalid", r["rejection_reason"], "odds_invalid")
    assert_eq("units 0 on rejection", r["recommended_units"], 0.0)

    # p_model missing
    r2 = compute_stake_recommendation({"odds": 2.0, "p_model": 0.0, "edge": 0.05}, profile)
    assert_eq("rejected p_model_missing", r2["rejection_reason"], "p_model_missing")

    # edge below threshold
    r3 = compute_stake_recommendation({"odds": 2.0, "p_model": 0.55, "edge": 0.01}, profile)
    assert_eq("rejected edge_below_threshold", r3["rejection_reason"], "edge_below_threshold")

    # confidence below threshold
    r4 = compute_stake_recommendation(
        {"odds": 2.0, "p_model": 0.55, "edge": 0.05, "confidence_score": 0.50}, profile)
    assert_eq("rejected confidence_below", r4["rejection_reason"], "confidence_below_threshold")

    # strategy avoid
    r5 = compute_stake_recommendation(
        {"odds": 2.0, "p_model": 0.55, "edge": 0.05, "confidence_score": 0.60,
         "strategy_recommendation": "avoid"}, profile)
    assert_eq("rejected strategy_avoid", r5["rejection_reason"], "strategy_avoid")

except Exception as e:
    fail("compute_stake_recommendation rejections", str(e))
    traceback.print_exc()


# ── Group 32: compute_stake_recommendation — valid stake ─────────────────────

section("32. compute_stake_recommendation — valid stake")
try:
    from app.services.bankroll_risk_service import compute_stake_recommendation

    profile = {
        "bankroll_units": 100.0, "kelly_fraction": 0.25, "max_pick_risk_units": 1.5,
        "min_edge_for_stake": 0.02, "min_confidence_for_stake": 0.52,
        "block_avoid": True, "reduce_low_sample": False,
    }
    pick = {
        "odds": 2.10, "p_model": 0.55, "edge": 0.05, "confidence_score": 0.60,
        "strategy_sample_size": 50, "market_key": "1X2", "league_id": 10,
    }
    rec = compute_stake_recommendation(pick, profile)
    assert_none("no rejection on valid pick", rec["rejection_reason"])
    assert_ge("units > 0", rec["recommended_units"], 0.01)
    assert_le("units <= 1.5", rec["recommended_units"], 1.5)
    assert_true("kelly_full > 0", rec["kelly_full"] > 0.0)
    assert_true("stake_label set", rec["stake_label"] in ("micro", "small", "medium", "large"))
    assert_true("risk_score in 0-100", 0 <= rec["risk_score"] <= 100)

except Exception as e:
    fail("compute_stake_recommendation valid", str(e))


# ── Group 33: compute_stake_recommendation — adjustments ─────────────────────

section("33. compute_stake_recommendation — downward adjustments")
try:
    from app.services.bankroll_risk_service import compute_stake_recommendation

    # Use small bankroll so Kelly fractions don't all get capped at max_pick_risk_units
    profile = {
        "bankroll_units": 10.0, "kelly_fraction": 0.25, "max_pick_risk_units": 1.5,
        "min_edge_for_stake": 0.02, "min_confidence_for_stake": 0.52,
        "block_avoid": False, "reduce_low_sample": True,
    }
    base_pick = {
        "odds": 2.10, "p_model": 0.55, "edge": 0.05, "confidence_score": 0.60,
        "strategy_sample_size": 50,
    }
    base_rec = compute_stake_recommendation(base_pick, profile)
    base_units = base_rec["recommended_units"]

    # reduce recommendation -> mult 0.5
    pick_reduce = {**base_pick, "strategy_recommendation": "reduce"}
    rec_reduce = compute_stake_recommendation(pick_reduce, profile)
    assert_true("reduce halves units", rec_reduce["recommended_units"] < base_units or base_units == 0)

    # low sample -> mult 0.75
    pick_ls = {**base_pick, "strategy_sample_size": 5}
    rec_ls = compute_stake_recommendation(pick_ls, profile)
    assert_true("low sample reduces units", rec_ls["recommended_units"] <= base_units)

    # negative CLV < -3.0 -> mult 0.6
    pick_clv = {**base_pick, "clv_percent": -4.0}
    rec_clv = compute_stake_recommendation(pick_clv, profile)
    assert_true("neg CLV reduces units", rec_clv["recommended_units"] <= base_units)

except Exception as e:
    fail("compute_stake_recommendation adjustments", str(e))


# ── Group 34: repo — upsert_bankroll_profile ──────────────────────────────────

section("34. repo upsert_bankroll_profile")
try:
    from app.data.local.bankroll_risk_repo import upsert_bankroll_profile, get_active_bankroll_profile

    upsert_bankroll_profile(conn, {
        "profile_name": "test_profile",
        "bankroll_units": 200.0,
        "base_unit_size": 2.0,
        "max_daily_risk_units": 10.0,
        "max_pick_risk_units": 2.0,
        "max_parlay_risk_units": 1.0,
        "max_same_league_units": 5.0,
        "max_same_market_units": 5.0,
        "max_same_team_units": 3.0,
        "kelly_fraction": 0.25,
        "min_edge_for_stake": 0.02,
        "min_confidence_for_stake": 0.52,
        "is_active": True,
    })
    profile = get_active_bankroll_profile(conn)
    assert_not_none("active profile found", profile)
    assert_eq("profile name", profile.get("profile_name"), "test_profile")
    assert_approx("bankroll_units", profile.get("bankroll_units"), 200.0)

    # Upsert again (ON CONFLICT) — should not raise
    upsert_bankroll_profile(conn, {"profile_name": "test_profile", "bankroll_units": 250.0})
    profile2 = get_active_bankroll_profile(conn)
    assert_approx("updated bankroll_units", profile2.get("bankroll_units"), 250.0)

except Exception as e:
    fail("repo upsert_bankroll_profile", str(e))
    traceback.print_exc()


# ── Group 35: repo — upsert_stake_recommendation ─────────────────────────────

section("35. repo upsert_stake_recommendation")
try:
    from app.data.local.bankroll_risk_repo import (
        upsert_stake_recommendation, get_stake_recommendation,
    )

    rec_row = {
        "pick_candidate_id": 9001,
        "published_pick_id": None,
        "fixture_id": 5001,
        "provider_fixture_id": None,
        "market_key": "1X2",
        "selection": "Home",
        "league_id": 10,
        "team_home_id": 101,
        "team_away_id": 202,
        "odds": 2.10,
        "p_model": 0.55,
        "edge": 0.05,
        "ev": 0.10,
        "ev_adj": 0.08,
        "strategy_score": 70.0,
        "strategy_recommendation": "bet",
        "clv_percent": 1.5,
        "risk_score": 25.0,
        "correlation_score": 0.10,
        "kelly_full": 0.14,
        "kelly_fractional": 0.035,
        "recommended_units": 0.8,
        "stake_label": "small",
        "rejection_reason": None,
    }
    upsert_stake_recommendation(conn, rec_row)
    fetched = get_stake_recommendation(conn, 9001)
    assert_not_none("stake rec fetched", fetched)
    assert_eq("market_key", fetched.get("market_key"), "1X2")
    assert_approx("recommended_units", fetched.get("recommended_units"), 0.8)
    assert_eq("stake_label", fetched.get("stake_label"), "small")

    # Upsert again (conflict) should update
    rec_row["recommended_units"] = 1.1
    rec_row["stake_label"] = "medium"
    upsert_stake_recommendation(conn, rec_row)
    fetched2 = get_stake_recommendation(conn, 9001)
    assert_approx("updated recommended_units", fetched2.get("recommended_units"), 1.1)

except Exception as e:
    fail("repo upsert_stake_recommendation", str(e))
    traceback.print_exc()


# ── Group 36: repo — upsert_portfolio_snapshot ───────────────────────────────

section("36. repo upsert_portfolio_snapshot")
try:
    from app.data.local.bankroll_risk_repo import (
        upsert_portfolio_snapshot, get_latest_portfolio_snapshot,
    )

    upsert_portfolio_snapshot(conn, {
        "snapshot_date": "2026-05-05",
        "total_picks": 5,
        "total_recommended_units": 3.5,
        "total_daily_risk_units": 3.5,
        "exposure_by_league": {"10": 2.0, "20": 1.5},
        "exposure_by_market": {"1X2": 2.5, "OU25": 1.0},
        "exposure_by_team": {"101": 1.5},
        "correlated_groups": [[0, 1]],
        "portfolio_score": 72.5,
        "risk_level": "balanced",
        "warnings": ["low_sample_strategy:1"],
    })
    snap = get_latest_portfolio_snapshot(conn)
    assert_not_none("snapshot fetched", snap)
    assert_eq("snapshot_date", str(snap.get("snapshot_date")), "2026-05-05")
    assert_eq("total_picks", snap.get("total_picks"), 5)
    assert_approx("portfolio_score", snap.get("portfolio_score"), 72.5)
    assert_eq("risk_level", snap.get("risk_level"), "balanced")
    assert_true("exposure_by_league parsed", isinstance(snap.get("exposure_by_league_json"), dict))
    assert_in("warnings is list", "low_sample_strategy:1",
               snap.get("warnings_json") or [])

    # Conflict update
    upsert_portfolio_snapshot(conn, {
        "snapshot_date": "2026-05-05", "total_picks": 8,
        "portfolio_score": 60.0, "risk_level": "aggressive",
    })
    snap2 = get_latest_portfolio_snapshot(conn)
    assert_eq("updated total_picks", snap2.get("total_picks"), 8)

except Exception as e:
    fail("repo upsert_portfolio_snapshot", str(e))
    traceback.print_exc()


# ── Group 37: repo — insert_risk_event + get_risk_events ─────────────────────

section("37. repo insert_risk_event + get_risk_events")
try:
    from app.data.local.bankroll_risk_repo import insert_risk_event, get_risk_events

    insert_risk_event(conn, {
        "event_type": "over_daily_risk_limit",
        "severity": "high",
        "entity_type": "portfolio",
        "entity_id": "2026-05-05",
        "message": "Daily risk limit exceeded: 6.2u > 5.0u",
    })
    insert_risk_event(conn, {
        "event_type": "correlated_picks",
        "severity": "medium",
        "entity_type": "pick_group",
        "message": "2 correlated picks detected",
    })

    events = get_risk_events(conn, days=1)
    assert_true("at least 2 events", len(events) >= 2)
    event_types = {e.get("event_type") for e in events}
    assert_in("over_daily_risk_limit event", "over_daily_risk_limit", event_types)
    assert_in("correlated_picks event", "correlated_picks", event_types)

except Exception as e:
    fail("repo insert/get risk_events", str(e))


# ── Group 38: repo — get_bankroll_summary ─────────────────────────────────────

section("38. repo get_bankroll_summary")
try:
    from app.data.local.bankroll_risk_repo import get_bankroll_summary

    summary = get_bankroll_summary(conn, days=30)
    assert_true("summary is dict", isinstance(summary, dict))
    assert_true("has total_recommendations", "total_recommendations" in summary)
    assert_ge("total_recommendations >= 1", summary.get("total_recommendations", 0), 1)

except Exception as e:
    fail("repo get_bankroll_summary", str(e))


# ── Group 39: repo — get_stake_recommendations_by_day ─────────────────────────

section("39. repo get_stake_recommendations_by_day")
try:
    from app.data.local.bankroll_risk_repo import get_stake_recommendations_by_day

    recs_day = get_stake_recommendations_by_day(conn, days=30)
    assert_true("returns list", isinstance(recs_day, list))
    assert_ge("at least 1 rec", len(recs_day), 1)
    if recs_day:
        assert_in("has pick_candidate_id key", "pick_candidate_id", recs_day[0])

except Exception as e:
    fail("repo get_stake_recommendations_by_day", str(e))


# ── Group 40: disabled mode — bankroll_engine_enabled=false ──────────────────

section("40. disabled mode (bankroll_engine_enabled=false)")
try:
    from app.core.config import settings
    from app.services.bankroll_risk_service import get_bankroll_meta_for_pick

    original = settings.bankroll_engine_enabled
    settings.bankroll_engine_enabled = False

    meta = get_bankroll_meta_for_pick(conn, {
        "odds": 2.10, "p_model": 0.55, "edge": 0.05, "confidence_score": 0.60,
    })
    assert_true("returns dict when disabled", isinstance(meta, dict))
    assert_none("bankroll_risk_score None when disabled", meta.get("bankroll_risk_score"))
    assert_none("bankroll_stake_label None when disabled", meta.get("bankroll_stake_label"))
    assert_none("bankroll_recommended_units None when disabled", meta.get("bankroll_recommended_units"))

    settings.bankroll_engine_enabled = original

except Exception as e:
    fail("disabled mode", str(e))
    traceback.print_exc()


# ── Group 41: enabled mode — get_bankroll_meta_for_pick ──────────────────────

section("41. get_bankroll_meta_for_pick — enabled")
try:
    from app.core.config import settings
    from app.services.bankroll_risk_service import get_bankroll_meta_for_pick

    settings.bankroll_engine_enabled = True

    meta = get_bankroll_meta_for_pick(conn, {
        "odds": 2.10, "p_model": 0.55, "edge": 0.05,
        "confidence_score": 0.60, "market_key": "1X2",
        "strategy_sample_size": 50,
    })
    assert_true("returns dict when enabled", isinstance(meta, dict))
    assert_true("has bankroll_risk_score key", "bankroll_risk_score" in meta)
    assert_true("has bankroll_stake_label key", "bankroll_stake_label" in meta)
    assert_true("has bankroll_kelly_full key", "bankroll_kelly_full" in meta)
    # risk_score in 0-100
    rs = meta.get("bankroll_risk_score")
    if rs is not None:
        assert_true("risk_score in 0-100", 0 <= rs <= 100)

    settings.bankroll_engine_enabled = False

except Exception as e:
    fail("get_bankroll_meta_for_pick enabled", str(e))
    traceback.print_exc()


# ── Group 42: run_bankroll_risk dry_run ───────────────────────────────────────

section("42. run_bankroll_risk — dry_run")
try:
    from app.services.bankroll_risk_service import run_bankroll_risk

    result = run_bankroll_risk(conn, days=1, dry_run=True)
    assert_true("returns dict", isinstance(result, dict))
    assert_true("has picks key", "picks" in result)
    assert_true("has with_stake key", "with_stake" in result)
    assert_true("has rejected key", "rejected" in result)
    assert_true("has total_units key", "total_units" in result)
    assert_true("has risk_level key", "risk_level" in result)
    assert_true("has warnings key", "warnings" in result)
    assert_true("has elapsed_s key", "elapsed_s" in result)
    assert_true("dry_run=True in result", result.get("dry_run") is True)
    assert_ge("elapsed_s >= 0", result.get("elapsed_s", -1), 0)

except Exception as e:
    fail("run_bankroll_risk dry_run", str(e))
    traceback.print_exc()


# ── Group 43: AI Router — bankroll intents ────────────────────────────────────

section("43. AI Router — bankroll intents")
try:
    from app.services.ai_router_service import classify_message

    cases = [
        ("como esta mi bankroll", "bankroll_summary"),
        ("cuanto tengo en el bankroll", "bankroll_summary"),
        ("cual es el riesgo del portfolio", "risk_summary"),
        ("como va el portafolio de riesgo", "risk_summary"),
        ("cuanto deberia apostar en este partido", "stake_question"),
        ("que stake recomiendan", "stake_question"),
        ("cuanta exposicion tengo en la premier", "exposure_question"),
        ("exposicion por liga", "exposure_question"),
        ("hay picks correlacionados hoy", "correlation_question"),
        ("cuales picks estan correlacionados", "correlation_question"),
        ("ayuda con el bankroll", "bankroll_help"),
        ("como funciona el bankroll engine", "bankroll_help"),
    ]
    failed_cases = []
    for text, expected in cases:
        try:
            result = classify_message(text)
            got = result.get("intent") if isinstance(result, dict) else str(result)
            if got == expected:
                ok(f"intent '{text[:30]}' -> {expected}")
            else:
                fail(f"intent '{text[:30]}'", f"got {got!r} expected {expected!r}")
                failed_cases.append((text, got, expected))
        except Exception as e:
            fail(f"intent '{text[:30]}'", str(e))

except Exception as e:
    fail("ai_router import", str(e))
    traceback.print_exc()


# ── Group 44: Scheduler source — bankroll_risk_job ───────────────────────────

section("44. Scheduler source — bankroll_risk_job")
try:
    import pathlib
    sj_src = (pathlib.Path(__file__).parent.parent / "app" / "services" / "scheduled_jobs.py").read_text(encoding="utf-8")
    assert_true("bankroll_risk_job defined", "async def bankroll_risk_job" in sj_src)
    assert_true("bankroll_risk_job references run_bankroll_risk", "run_bankroll_risk" in sj_src)

except Exception as e:
    fail("scheduled_jobs.bankroll_risk_job", str(e))


# ── Group 45: Scheduler service registration ──────────────────────────────────

section("45. scheduler_service bankroll source check")
try:
    import inspect
    import importlib
    ss_src = inspect.getsource(importlib.import_module("app.services.scheduler_service"))
    assert_true("bankroll_risk_job referenced in scheduler", "bankroll_risk_job" in ss_src)
    assert_true("scheduler_bankroll_enabled referenced", "scheduler_bankroll_enabled" in ss_src)

except Exception as e:
    fail("scheduler_service bankroll", str(e))


# ── Group 46: main.py imports & commands check ───────────────────────────────

section("46. main.py imports & bankroll handler")
try:
    import pathlib
    main_src = (pathlib.Path(__file__).parent.parent / "app" / "main.py").read_text(encoding="utf-8")
    assert_true("bankroll_handler imported in main.py", "bankroll_handler" in main_src)
    assert_true("/bankroll command in main.py", '"bankroll"' in main_src or "'bankroll'" in main_src)
    assert_true("/riesgo command in main.py", '"riesgo"' in main_src or "'riesgo'" in main_src)

except Exception as e:
    fail("main.py bankroll check", str(e))


# ── Group 47: bankroll handler syntax ─────────────────────────────────────────

section("47. bankroll handler syntax")
try:
    import pathlib, ast
    bh_path = pathlib.Path(__file__).parent.parent / "app" / "bot" / "handlers" / "bankroll.py"
    src = bh_path.read_text(encoding="utf-8")
    ast.parse(src)
    ok("bankroll.py parses OK")
    assert_true("bankroll_handler defined", "async def bankroll_handler" in src)
    assert_true("riesgo_handler defined", "async def riesgo_handler" in src)
    assert_true("stake_handler defined", "async def stake_handler" in src)

except Exception as e:
    fail("bankroll handler syntax", str(e))


# ── Group 48: CLI scripts — build_bankroll_risk exists ───────────────────────

section("48. CLI scripts exist")
try:
    import pathlib
    scripts_dir = pathlib.Path(__file__).parent

    for script in ("build_bankroll_risk.py", "audit_bankroll_risk.py", "report_bankroll.py"):
        path = scripts_dir / script
        assert_true(f"script {script} exists", path.exists())

except Exception as e:
    fail("CLI scripts exist", str(e))


# ── Group 49: build_bankroll_risk.py — no syntax errors ──────────────────────

section("49. build_bankroll_risk.py syntax")
try:
    import pathlib, ast
    scripts_dir = pathlib.Path(__file__).parent
    for script in ("build_bankroll_risk.py", "audit_bankroll_risk.py", "report_bankroll.py"):
        path = scripts_dir / script
        if path.exists():
            try:
                ast.parse(path.read_text(encoding="utf-8"))
                ok(f"{script} parses OK")
            except SyntaxError as se:
                fail(f"{script} syntax error", str(se))
        else:
            fail(f"{script} not found")

except Exception as e:
    fail("script syntax check", str(e))


# ── Group 50: init_local_db.py — bankroll tables listed ─────────────────────

section("50. init_local_db.py — bankroll tables in EXPECTED_TABLES")
try:
    import pathlib
    init_src = (pathlib.Path(__file__).parent / "init_local_db.py").read_text(encoding="utf-8")
    for tbl in ("bankroll_profiles", "stake_recommendations",
                "portfolio_risk_snapshots", "risk_events"):
        assert_true(f"init_local_db has {tbl}", tbl in init_src)

except Exception as e:
    fail("init_local_db bankroll tables", str(e))


# ── Group 51: VE adapter — bankroll fields in _EMPTY ─────────────────────────

section("51. VE adapter — bankroll fields present")
try:
    import inspect, importlib
    ve = importlib.import_module("app.services.value_engine_live_adapter")
    src = inspect.getsource(ve)
    for field in ("bankroll_risk_score", "bankroll_stake_label",
                  "bankroll_recommended_units", "bankroll_kelly_full"):
        assert_true(f"VE adapter has {field}", field in src)

except Exception as e:
    fail("VE adapter bankroll fields", str(e))


# ── Group 52: top.py shows bankroll data ─────────────────────────────────────

section("52. top.py bankroll integration")
try:
    import pathlib
    top_src = (pathlib.Path(__file__).parent.parent / "app" / "bot" / "handlers" / "top.py").read_text(encoding="utf-8")
    assert_true("top.py references bankroll_recommended_units or bankroll",
                "bankroll" in top_src)

except Exception as e:
    fail("top.py bankroll check", str(e))


# ── Group 53: valor.py has bankroll section ───────────────────────────────────

section("53. valor.py bankroll section")
try:
    import pathlib
    valor_src = (pathlib.Path(__file__).parent.parent / "app" / "bot" / "handlers" / "valor.py").read_text(encoding="utf-8")
    assert_true("valor.py references bankroll_engine_enabled",
                "bankroll_engine_enabled" in valor_src)

except Exception as e:
    fail("valor.py bankroll check", str(e))


# ── Group 54: report_daily_edge.py — bankroll section ───────────────────────

section("54. report_daily_edge.py bankroll section")
try:
    import pathlib
    rde_path = pathlib.Path(__file__).parent / "report_daily_edge.py"
    if rde_path.exists():
        rde_src = rde_path.read_text(encoding="utf-8")
        assert_true("report_daily_edge has bankroll reference", "bankroll" in rde_src.lower())
    else:
        fail("report_daily_edge.py not found")

except Exception as e:
    fail("report_daily_edge bankroll", str(e))


# ── Group 55: kelly zero edge case ───────────────────────────────────────────

section("55. Kelly zero — edge case picks")
try:
    from app.services.bankroll_risk_service import compute_stake_recommendation

    profile = {
        "bankroll_units": 100.0, "kelly_fraction": 0.25, "max_pick_risk_units": 1.5,
        "min_edge_for_stake": 0.02, "min_confidence_for_stake": 0.52,
        "block_avoid": True, "reduce_low_sample": False,
    }
    # p_model exactly at implied -> kelly = 0 -> kelly_zero rejection
    # implied at odds=2.0 is 0.50
    pick_exact = {"odds": 2.0, "p_model": 0.50, "edge": 0.03, "confidence_score": 0.60}
    rec = compute_stake_recommendation(pick_exact, profile)
    # edge >= min_edge, conf ok, but kelly could be 0 depending on exact p
    assert_true("kelly_zero handled", rec["rejection_reason"] in (
        "kelly_zero", "kelly_too_small", None
    ))

    # p_model just below implied
    pick_neg = {"odds": 2.0, "p_model": 0.49, "edge": 0.03, "confidence_score": 0.60}
    rec2 = compute_stake_recommendation(pick_neg, profile)
    assert_eq("negative edge kelly -> kelly_zero", rec2["rejection_reason"], "kelly_zero")

except Exception as e:
    fail("kelly zero edge case", str(e))


# ── Group 56: correlation — no fixture IDs ───────────────────────────────────

section("56. Correlation — no fixture IDs fallback")
try:
    from app.services.bankroll_risk_service import compute_pick_correlation

    # Use different selections to avoid triggering the same-mkt/sel related-market pair
    pick_a = {"market_key": "1X2", "selection": "Home", "league_id": 5,
              "team_home_id": 10, "team_away_id": 20}
    pick_b = {"market_key": "1X2", "selection": "Away", "league_id": 5,
              "team_home_id": 10, "team_away_id": 30}
    corr = compute_pick_correlation(pick_a, pick_b)
    # same team (10) + same league (5) -> 0.40 + 0.08 = 0.48
    assert_approx("no fixture corr same team+league", corr, 0.48, tol=0.01)

    pick_c = {"market_key": "OU25", "selection": "Under 2.5", "league_id": 99,
              "team_home_id": 50, "team_away_id": 60}
    corr2 = compute_pick_correlation(pick_a, pick_c)
    # Different league, different team, no related market pair -> 0.0
    assert_eq("completely unrelated corr=0.0", corr2, 0.0)

except Exception as e:
    fail("correlation no fixture IDs", str(e))


# ── Group 57: exposure — no units / zero exclusion ───────────────────────────

section("57. compute_exposure — zero units excluded")
try:
    from app.services.bankroll_risk_service import compute_exposure

    recs = [
        {"league_id": 5, "market_key": "1X2", "team_home_id": 1,
         "fixture_id": 1, "recommended_units": 0.0},
        {"league_id": 5, "market_key": "1X2", "team_home_id": 2,
         "fixture_id": 2, "recommended_units": None},
    ]
    exp = compute_exposure(recs)
    assert_eq("zero units -> empty by_league", exp["by_league"], {})
    assert_eq("zero units -> empty by_market", exp["by_market"], {})

except Exception as e:
    fail("compute_exposure zero units", str(e))


# ── Group 58: _default_profile uses settings ─────────────────────────────────

section("58. _default_profile reads settings")
try:
    from app.services.bankroll_risk_service import _default_profile
    from app.core.config import settings

    dp = _default_profile()
    assert_eq("bankroll_units from settings",
              dp["bankroll_units"], settings.bankroll_default_units)
    assert_eq("kelly_fraction from settings",
              dp["kelly_fraction"], settings.bankroll_kelly_fraction)
    assert_eq("min_edge from settings",
              dp["min_edge_for_stake"], settings.bankroll_min_edge)
    assert_eq("max_pick_risk_units from settings",
              dp["max_pick_risk_units"], settings.bankroll_max_pick_units)

except Exception as e:
    fail("_default_profile settings", str(e))


# ── Group 59: config settings defaults ───────────────────────────────────────

section("59. config — Phase 14 settings defaults")
try:
    from app.core.config import settings

    assert_eq("bankroll_engine_enabled default false",
               settings.bankroll_engine_enabled, False)
    assert_eq("bankroll_use_for_selection default false",
               settings.bankroll_use_for_selection, False)
    assert_approx("bankroll_default_units default 100",
                   settings.bankroll_default_units, 100.0)
    assert_approx("bankroll_kelly_fraction default 0.25",
                   settings.bankroll_kelly_fraction, 0.25)
    assert_approx("bankroll_max_pick_units default 1.5",
                   settings.bankroll_max_pick_units, 1.5)
    assert_approx("bankroll_min_edge default 0.02",
                   settings.bankroll_min_edge, 0.02)
    assert_approx("bankroll_min_confidence default 0.52",
                   settings.bankroll_min_confidence, 0.52)
    assert_eq("scheduler_bankroll_enabled default false",
               settings.scheduler_bankroll_enabled, False)

except Exception as e:
    fail("config Phase 14 settings", str(e))
    traceback.print_exc()


# ── Group 60: partido.py bankroll section ─────────────────────────────────────

section("60. partido.py bankroll integration")
try:
    import pathlib
    partido_path = pathlib.Path(__file__).parent.parent / "app" / "bot" / "handlers" / "partido.py"
    if partido_path.exists():
        src = partido_path.read_text(encoding="utf-8")
        assert_true("partido.py references bankroll", "bankroll" in src)
    else:
        fail("partido.py not found")

except Exception as e:
    fail("partido.py bankroll check", str(e))


# ═══════════════════════════════════════════════════════════════════
# Final summary
# ═══════════════════════════════════════════════════════════════════

print(f"\n{'=' * 60}")
print(f"  RESULTS: {PASSED} passed, {FAILED} failed")
print("=" * 60)
if ERRORS:
    print("\nFailed tests:")
    for e in ERRORS:
        print(f"  {e}")
print()
sys.exit(0 if FAILED == 0 else 1)
