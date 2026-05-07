#!/usr/bin/env python
"""Phase 13: Strategy Learning test suite.

120+ tests across 28 groups, all in-memory DuckDB (no Supabase, no API calls).
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
    print('=' * 60)


def ok(name):
    global PASSED
    PASSED += 1
    print(f"  PASS  {name}")


def fail(name, detail=""):
    global FAILED
    FAILED += 1
    msg = f"  FAIL  {name}"
    if detail:
        msg += f" — {detail}"
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


def assert_approx(name, got, expected, tol=0.01):
    if got is None or expected is None:
        fail(name, f"None value: got={got} expected={expected}")
    elif abs(got - expected) <= tol:
        ok(name)
    else:
        fail(name, f"got {got} expected {expected} tol {tol}")


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

    for tbl in ("strategy_profiles", "strategy_learning_runs",
                "strategy_adjustments", "pick_learning_annotations"):
        assert_true(f"table {tbl} exists", tbl in table_names)
except Exception as e:
    fail("schema setup", str(e))
    traceback.print_exc()


# ── Group 2: Sequences ────────────────────────────────────────────────────────

section("2. Sequences")
try:
    for seq in ("strategy_profiles_seq", "strategy_learning_runs_seq",
                "strategy_adjustments_seq", "pick_learning_annotations_seq"):
        try:
            v = conn.execute(f"SELECT nextval('{seq}')").fetchone()[0]
            assert_true(f"seq {seq} works", v >= 1)
        except Exception as e:
            fail(f"seq {seq}", str(e))
except Exception as e:
    fail("sequences group", str(e))


# ── Group 3: odds_bucket ──────────────────────────────────────────────────────

section("3. odds_bucket")
try:
    from app.services.strategy_learning_service import odds_bucket
    assert_eq("odds_bucket None", odds_bucket(None), "odds_unknown")
    assert_eq("odds_bucket 1.30", odds_bucket(1.30), "odds_lt1.50")
    assert_eq("odds_bucket 1.50", odds_bucket(1.50), "odds_1.50_1.90")
    assert_eq("odds_bucket 1.85", odds_bucket(1.85), "odds_1.50_1.90")
    assert_eq("odds_bucket 2.10", odds_bucket(2.10), "odds_1.90_2.50")
    assert_eq("odds_bucket 3.00", odds_bucket(3.00), "odds_2.50_3.50")
    assert_eq("odds_bucket 4.00", odds_bucket(4.00), "odds_gt3.50")
except Exception as e:
    fail("odds_bucket import", str(e))


# ── Group 4: edge_bucket ──────────────────────────────────────────────────────

section("4. edge_bucket")
try:
    from app.services.strategy_learning_service import edge_bucket
    assert_eq("edge_bucket None", edge_bucket(None), "edge_unknown")
    assert_eq("edge_bucket -0.05", edge_bucket(-0.05), "edge_neg")
    assert_eq("edge_bucket 0.00", edge_bucket(0.00), "edge_0_2")
    assert_eq("edge_bucket 0.015", edge_bucket(0.015), "edge_0_2")
    assert_eq("edge_bucket 0.03", edge_bucket(0.03), "edge_2_5")
    assert_eq("edge_bucket 0.07", edge_bucket(0.07), "edge_5_10")
    assert_eq("edge_bucket 0.15", edge_bucket(0.15), "edge_gt10")
except Exception as e:
    fail("edge_bucket import", str(e))


# ── Group 5: confidence_bucket ────────────────────────────────────────────────

section("5. confidence_bucket")
try:
    from app.services.strategy_learning_service import confidence_bucket
    assert_eq("conf_bucket None", confidence_bucket(None), "conf_unknown")
    assert_eq("conf_bucket 0.48", confidence_bucket(0.48), "conf_lt50")
    assert_eq("conf_bucket 0.52", confidence_bucket(0.52), "conf_50_55")
    assert_eq("conf_bucket 0.57", confidence_bucket(0.57), "conf_55_60")
    assert_eq("conf_bucket 0.65", confidence_bucket(0.65), "conf_60_70")
    assert_eq("conf_bucket 0.75", confidence_bucket(0.75), "conf_gt70")
except Exception as e:
    fail("confidence_bucket import", str(e))


# ── Group 6: clv_bucket ───────────────────────────────────────────────────────

section("6. clv_bucket")
try:
    from app.services.strategy_learning_service import clv_bucket
    assert_eq("clv_bucket None", clv_bucket(None), "clv_unknown")
    assert_eq("clv_bucket -5.0", clv_bucket(-5.0), "clv_negative")
    assert_eq("clv_bucket -1.0", clv_bucket(-1.0), "clv_slightly_neg")
    assert_eq("clv_bucket 0.5", clv_bucket(0.5), "clv_neutral")
    assert_eq("clv_bucket 3.0", clv_bucket(3.0), "clv_small_pos")
    assert_eq("clv_bucket 6.0", clv_bucket(6.0), "clv_strong_pos")
except Exception as e:
    fail("clv_bucket import", str(e))


# ── Group 7: roi_bucket ───────────────────────────────────────────────────────

section("7. roi_bucket")
try:
    from app.services.strategy_learning_service import roi_bucket
    assert_eq("roi_bucket None", roi_bucket(None), "roi_unknown")
    assert_eq("roi_bucket -0.30", roi_bucket(-0.30), "roi_very_neg")
    assert_eq("roi_bucket -0.05", roi_bucket(-0.05), "roi_neg")
    assert_eq("roi_bucket 0.02", roi_bucket(0.02), "roi_flat")
    assert_eq("roi_bucket 0.08", roi_bucket(0.08), "roi_good")
    assert_eq("roi_bucket 0.25", roi_bucket(0.25), "roi_excellent")
except Exception as e:
    fail("roi_bucket import", str(e))


# ── Group 8: sample_bucket ────────────────────────────────────────────────────

section("8. sample_bucket")
try:
    from app.services.strategy_learning_service import sample_bucket
    assert_eq("sample_bucket 0", sample_bucket(0), "sample_tiny")
    assert_eq("sample_bucket 5", sample_bucket(5), "sample_tiny")
    assert_eq("sample_bucket 20", sample_bucket(20), "sample_small")
    assert_eq("sample_bucket 60", sample_bucket(60), "sample_medium")
    assert_eq("sample_bucket 200", sample_bucket(200), "sample_large")
except Exception as e:
    fail("sample_bucket import", str(e))


# ── Group 9: build_strategy_key ───────────────────────────────────────────────

section("9. build_strategy_key")
try:
    from app.services.strategy_learning_service import build_strategy_key
    k = build_strategy_key("OU25", 140, 2.10, 0.03, 0.57, 3.0)
    assert_true("key starts with OU25", k.startswith("OU25|"))
    assert_true("key has league_140", "league_140" in k)
    assert_true("key has odds bucket", "odds_1.90_2.50" in k)
    assert_true("key has edge bucket", "edge_2_5" in k)
    assert_true("key has conf bucket", "conf_55_60" in k)
    assert_true("key has clv bucket", "clv_small_pos" in k)

    k_global = build_strategy_key("1X2", None, 1.85, 0.05, 0.60, -1.5)
    assert_true("global scope", "global" in k_global)

    k_mv = build_strategy_key("BTTS", 39, 2.0, 0.03, 0.58, 2.0,
                               movement_label="steam_towards_selection")
    assert_true("movement label included", "mv_steam_towards_selection" in k_mv)

    k_stable = build_strategy_key("BTTS", 39, 2.0, 0.03, 0.58, 2.0,
                                   movement_label="stable")
    assert_true("stable mv not included", "mv_stable" not in k_stable)

    k_avail = build_strategy_key("1X2", 140, 2.0, 0.04, 0.60, 1.0,
                                  availability_level="high")
    assert_true("avail_high included", "avail_high" in k_avail)
except Exception as e:
    fail("build_strategy_key", str(e))
    traceback.print_exc()


# ── Group 10: learning_label ──────────────────────────────────────────────────

section("10. learning_label")
try:
    from app.services.strategy_learning_service import _learning_label
    assert_eq("strong_positive: win+clv+", _learning_label("win", 0.8, 3.0, True), "strong_positive")
    assert_eq("positive: win no clv", _learning_label("win", 0.8, None, False), "positive")
    assert_eq("negative: loss+clv-", _learning_label("loss", -1.0, -3.0, False), "negative")
    assert_eq("weak: loss+clv+", _learning_label("loss", -1.0, 3.0, True), "weak")
    assert_eq("neutral: void", _learning_label("void", 0.0, 0.0, False), "neutral")
    assert_eq("no_data: pending", _learning_label("pending", None, None, None), "no_data")
    assert_eq("no_data: None status", _learning_label(None, None, None, None), "no_data")
    assert_eq("positive: win clv neutral", _learning_label("win", 0.5, 0.1, False), "positive")
except Exception as e:
    fail("learning_label", str(e))
    traceback.print_exc()


# ── Group 11: stability_score ─────────────────────────────────────────────────

section("11. stability_score")
try:
    from app.services.strategy_learning_service import _stability_score
    s_low = _stability_score(3, 0.1, 0.6)
    assert_approx("tiny sample stability", s_low, 0.3, tol=0.01)

    s_agree_pos = _stability_score(100, 0.10, 0.65)
    assert_true("agree pos > 0.5", s_agree_pos > 0.5)

    s_disagree = _stability_score(100, -0.10, 0.65)
    assert_true("disagree < agree_pos", s_disagree < s_agree_pos)
except Exception as e:
    fail("stability_score", str(e))


# ── Group 12: strategy_score small sample ─────────────────────────────────────

section("12. strategy_score (small sample cap)")
try:
    from app.services.strategy_learning_service import _compute_strategy_score
    score_small = _compute_strategy_score(15, 0.20, 0.70, 3.0, 0.65, 0.8)
    assert_true("small sample capped at 60", score_small <= 60.0)
    assert_true("small sample score > 0", score_small > 0.0)
except Exception as e:
    fail("strategy_score small sample", str(e))


# ── Group 13: strategy_score medium sample ────────────────────────────────────

section("13. strategy_score (medium sample)")
try:
    from app.services.strategy_learning_service import _compute_strategy_score
    score_med = _compute_strategy_score(40, 0.15, 0.65, 2.5, 0.62, 0.7)
    assert_true("medium sample capped at 75", score_med <= 75.0)
    assert_true("medium score > 0", score_med > 0.0)
except Exception as e:
    fail("strategy_score medium sample", str(e))


# ── Group 14: strategy_score full sample ──────────────────────────────────────

section("14. strategy_score (full sample)")
try:
    from app.services.strategy_learning_service import _compute_strategy_score
    score_full = _compute_strategy_score(100, 0.12, 0.62, 2.0, 0.60, 0.75)
    assert_true("full sample no cap", score_full <= 100.0)
    assert_true("full sample decent score", score_full > 50.0)
    score_zero = _compute_strategy_score(0, 0.0, 0.5, 0.0, 0.5, 0.5)
    assert_eq("zero sample = 0", score_zero, 0.0)
except Exception as e:
    fail("strategy_score full sample", str(e))


# ── Group 15: ROI positive + CLV negative ────────────────────────────────────

section("15. ROI+ CLV- scenario (high variance luck)")
try:
    from app.services.strategy_learning_service import _compute_strategy_score
    score_lucked = _compute_strategy_score(60, 0.10, 0.38, -2.5, 0.55, 0.35)
    score_solid  = _compute_strategy_score(60, 0.10, 0.62,  2.5, 0.60, 0.75)
    assert_true("lucked score < solid score", score_lucked < score_solid)
except Exception as e:
    fail("roi+ clv- scenario", str(e))


# ── Group 16: ROI negative + CLV positive ────────────────────────────────────

section("16. ROI- CLV+ scenario (bad variance, good process)")
try:
    from app.services.strategy_learning_service import _compute_strategy_score
    score_bad_luck = _compute_strategy_score(60, -0.05, 0.58, 2.0, 0.48, 0.6)
    score_full_bad = _compute_strategy_score(60, -0.20, 0.30, -4.0, 0.35, 0.2)
    assert_true("bad_luck > full_bad", score_bad_luck > score_full_bad)
except Exception as e:
    fail("roi- clv+ scenario", str(e))


# ── Group 17: recommendations ─────────────────────────────────────────────────

section("17. recommendation labels")
try:
    from app.services.strategy_learning_service import _compute_recommendation
    rec_insuf = _compute_recommendation(80.0, 5, 0.15, 3.0)
    assert_eq("insufficient_sample", rec_insuf, "insufficient_sample")

    rec_promote = _compute_recommendation(80.0, 60, 0.15, 3.0)
    assert_eq("promote with n=60", rec_promote, "promote")

    rec_monitor = _compute_recommendation(65.0, 60, 0.08, 1.5)
    assert_eq("monitor mid score", rec_monitor, "monitor")

    rec_neutral = _compute_recommendation(48.0, 60, 0.02, 0.0)
    assert_true("neutral range", rec_neutral in ("neutral", "reduce"))

    rec_avoid = _compute_recommendation(20.0, 60, -0.15, -3.0)
    assert_true("avoid or reduce for terrible stats",
                rec_avoid in ("avoid", "reduce"))
except Exception as e:
    fail("recommendations", str(e))
    traceback.print_exc()


# ── Group 18: upsert_strategy_profile ────────────────────────────────────────

section("18. upsert_strategy_profile")
try:
    from app.data.local.strategy_learning_repo import upsert_strategy_profile, get_strategy_by_key
    profile = {
        "strategy_key": "OU25|league_39|odds_1.90_2.50|edge_2_5|conf_55_60|clv_small_pos",
        "market_key":   "OU25",
        "league_id":    39,
        "league_name":  "Premier League",
        "scope":        "league_39",
        "odds_bucket":  "odds_1.90_2.50",
        "confidence_bucket": "conf_55_60",
        "edge_bucket":  "edge_2_5",
        "clv_bucket":   "clv_small_pos",
        "sample_size":  55,
        "wins": 30, "losses": 22, "voids": 3,
        "hit_rate": 0.577,
        "roi": 0.085,
        "avg_profit": 0.085,
        "avg_clv_percent": 2.3,
        "clv_beat_rate": 0.62,
        "avg_edge": 0.03,
        "avg_quality_score": 0.65,
        "avg_confidence": 0.57,
        "stability_score": 0.72,
        "strategy_score": 68.5,
        "recommendation": "monitor",
    }
    ok_r = upsert_strategy_profile(conn, profile)
    assert_true("upsert returns True", ok_r)
    fetched = get_strategy_by_key(conn, profile["strategy_key"])
    assert_true("profile fetched", fetched is not None)
    assert_eq("strategy_key", fetched["strategy_key"], profile["strategy_key"])
    assert_approx("strategy_score", fetched["strategy_score"], 68.5, tol=0.1)

    # Upsert again (idempotent)
    profile["strategy_score"] = 70.0
    upsert_strategy_profile(conn, profile)
    fetched2 = get_strategy_by_key(conn, profile["strategy_key"])
    assert_approx("upsert updates score", fetched2["strategy_score"], 70.0, tol=0.1)
except Exception as e:
    fail("upsert_strategy_profile", str(e))
    traceback.print_exc()


# ── Group 19: upsert_strategy_learning_run ────────────────────────────────────

section("19. upsert_strategy_learning_run")
try:
    from app.data.local.strategy_learning_repo import upsert_strategy_learning_run
    ok_r = upsert_strategy_learning_run(conn, {
        "run_key": "sl_test_run_001",
        "days": 30,
        "total_picks": 40,
        "profiles_created": 8,
        "profiles_updated": 4,
        "best_strategy_key": "OU25|league_39|odds_1.90_2.50|edge_2_5|conf_55_60|clv_small_pos",
        "worst_strategy_key": "1X2|global|odds_gt3.50|edge_neg|conf_lt50|clv_negative",
    })
    assert_true("upsert run returns True", ok_r)
    n = conn.execute("SELECT COUNT(*) FROM strategy_learning_runs WHERE run_key='sl_test_run_001'").fetchone()[0]
    assert_eq("run stored", n, 1)
except Exception as e:
    fail("upsert_strategy_learning_run", str(e))


# ── Group 20: upsert_strategy_adjustment ─────────────────────────────────────

section("20. upsert_strategy_adjustment")
try:
    from app.data.local.strategy_learning_repo import upsert_strategy_adjustment, get_strategy_adjustments
    ok_r = upsert_strategy_adjustment(conn, {
        "strategy_key":        "OU25|league_39|odds_1.90_2.50|edge_2_5|conf_55_60|clv_small_pos",
        "market_key":          "OU25",
        "league_id":           39,
        "adjustment_type":     "monitor",
        "adjustment_value":    0.0,
        "reason":              "Moderate score, watch for more data",
        "evidence_sample_size": 55,
        "evidence_roi":        0.085,
        "evidence_clv":        2.3,
        "status":              "pending",
    })
    assert_true("upsert adj returns True", ok_r)
    adjs = get_strategy_adjustments(conn, status="pending")
    assert_true("adjustment found pending", len(adjs) >= 1)
except Exception as e:
    fail("upsert_strategy_adjustment", str(e))


# ── Group 21: upsert_pick_learning_annotation ─────────────────────────────────

section("21. upsert_pick_learning_annotation")
try:
    from app.data.local.strategy_learning_repo import (
        upsert_pick_learning_annotation,
    )
    ok_r = upsert_pick_learning_annotation(conn, {
        "pick_candidate_id":  9901,
        "published_pick_id":  8801,
        "fixture_id":         1000,
        "strategy_key":       "OU25|league_39|odds_1.90_2.50|edge_2_5|conf_55_60|clv_small_pos",
        "market_key":         "OU25",
        "league_id":          39,
        "odds_bucket":        "odds_1.90_2.50",
        "confidence_bucket":  "conf_55_60",
        "edge_bucket":        "edge_2_5",
        "clv_bucket":         "clv_small_pos",
        "result_status":      "win",
        "profit":             0.9,
        "clv_percent":        2.8,
        "beat_closing_line":  True,
        "learning_label":     "strong_positive",
    })
    assert_true("upsert annotation returns True", ok_r)
    n = conn.execute("SELECT COUNT(*) FROM pick_learning_annotations WHERE pick_candidate_id=9901").fetchone()[0]
    assert_eq("annotation stored", n, 1)

    # Idempotent upsert
    ok_r2 = upsert_pick_learning_annotation(conn, {
        "pick_candidate_id":  9901,
        "result_status":      "win",
        "strategy_key":       "OU25|league_39|odds_1.90_2.50|edge_2_5|conf_55_60|clv_small_pos",
        "learning_label":     "strong_positive",
    })
    assert_true("idempotent upsert returns True", ok_r2)
    n2 = conn.execute("SELECT COUNT(*) FROM pick_learning_annotations WHERE pick_candidate_id=9901").fetchone()[0]
    assert_eq("still 1 row after re-upsert", n2, 1)
except Exception as e:
    fail("upsert_pick_learning_annotation", str(e))
    traceback.print_exc()


# ── Group 22: get_strategy_profiles ──────────────────────────────────────────

section("22. get_strategy_profiles")
try:
    from app.data.local.strategy_learning_repo import get_strategy_profiles
    profiles_all = get_strategy_profiles(conn)
    assert_true("profiles non-empty", len(profiles_all) >= 1)

    profiles_mkt = get_strategy_profiles(conn, market_key="OU25")
    for p in profiles_mkt:
        assert_eq("market_key filter", p["market_key"], "OU25")

    profiles_lg = get_strategy_profiles(conn, league_id=39)
    for p in profiles_lg:
        assert_eq("league_id filter", p["league_id"], 39)
except Exception as e:
    fail("get_strategy_profiles", str(e))


# ── Group 23: get_best/weak strategies ───────────────────────────────────────

section("23. get_best/weak strategies")
try:
    from app.data.local.strategy_learning_repo import get_best_strategies, get_weak_strategies

    # Seed a second profile with lower score
    from app.data.local.strategy_learning_repo import upsert_strategy_profile
    upsert_strategy_profile(conn, {
        "strategy_key": "1X2|global|odds_gt3.50|edge_neg|conf_lt50|clv_negative",
        "market_key":   "1X2",
        "league_id":    None,
        "scope":        "global",
        "sample_size":  20,
        "wins": 5, "losses": 14, "voids": 1,
        "hit_rate": 0.263,
        "roi": -0.18,
        "avg_clv_percent": -3.5,
        "clv_beat_rate": 0.28,
        "strategy_score": 22.0,
        "recommendation": "avoid",
    })

    best = get_best_strategies(conn, limit=5)
    assert_true("best strategies list", len(best) >= 1)
    if len(best) > 1:
        assert_true("best sorted descending",
                    best[0]["strategy_score"] >= best[-1]["strategy_score"])

    weak = get_weak_strategies(conn, limit=5)
    assert_true("weak strategies list", len(weak) >= 1)
except Exception as e:
    fail("get_best/weak strategies", str(e))
    traceback.print_exc()


# ── Group 24: get_learning_summary ───────────────────────────────────────────

section("24. get_learning_summary")
try:
    from app.data.local.strategy_learning_repo import get_learning_summary
    summary = get_learning_summary(conn)
    assert_true("summary is dict", isinstance(summary, dict))
    assert_true("total_profiles key", "total_profiles" in summary)
    assert_true("total_annotations key", "total_annotations" in summary)
    assert_true("profiles >= 1", (summary.get("total_profiles") or 0) >= 1)
    assert_true("annotations >= 1", (summary.get("total_annotations") or 0) >= 1)
    assert_true("best_strategy_key present", "best_strategy_key" in summary)
except Exception as e:
    fail("get_learning_summary", str(e))


# ── Group 25: audit_strategy_learning ────────────────────────────────────────

section("25. audit_strategy_learning")
try:
    from app.data.local.strategy_learning_repo import audit_strategy_learning
    audit = audit_strategy_learning(conn)
    assert_true("audit is dict", isinstance(audit, dict))
    assert_true("table_counts key", "table_counts" in audit)
    for t in ("strategy_profiles", "pick_learning_annotations"):
        cnt = audit["table_counts"].get(t, -1)
        assert_true(f"audit {t} >= 0", cnt >= 0)
except Exception as e:
    fail("audit_strategy_learning", str(e))


# ── Group 26: compute_strategy_profiles (service) ────────────────────────────

section("26. compute_strategy_profiles (from annotations)")
try:
    from app.services.strategy_learning_service import compute_strategy_profiles

    annotations = [
        {
            "pick_candidate_id": 1001 + i,
            "fixture_id": 2000 + i,
            "strategy_key": "OU25|league_140|odds_1.90_2.50|edge_2_5|conf_55_60|clv_small_pos",
            "market_key": "OU25",
            "league_id": 140,
            "odds_bucket": "odds_1.90_2.50",
            "confidence_bucket": "conf_55_60",
            "edge_bucket": "edge_2_5",
            "clv_bucket": "clv_small_pos",
            "result_status": "win" if i % 3 != 0 else "loss",
            "profit": 0.8 if i % 3 != 0 else -1.0,
            "clv_percent": 2.0 if i % 3 != 0 else -0.5,
            "beat_closing_line": i % 3 != 0,
            "learning_label": "strong_positive" if i % 3 != 0 else "negative",
            "metadata": {},
        }
        for i in range(15)
    ]
    profiles = compute_strategy_profiles(annotations)
    assert_true("profiles returned", len(profiles) == 1)
    p = profiles[0]
    assert_eq("profile strategy_key", p["strategy_key"],
              "OU25|league_140|odds_1.90_2.50|edge_2_5|conf_55_60|clv_small_pos")
    assert_eq("sample_size", p["sample_size"], 15)
    assert_true("wins > 0", p["wins"] > 0)
    assert_true("strategy_score computed", p["strategy_score"] is not None)
    assert_true("recommendation set", p["recommendation"] is not None)
except Exception as e:
    fail("compute_strategy_profiles", str(e))
    traceback.print_exc()


# ── Group 27: generate_strategy_adjustments ───────────────────────────────────

section("27. generate_strategy_adjustments")
try:
    from app.services.strategy_learning_service import (
        compute_strategy_profiles,
        generate_strategy_adjustments,
    )
    annotations_insuf = [
        {
            "pick_candidate_id": 3000 + i,
            "fixture_id": 4000 + i,
            "strategy_key": "BTTS|global|odds_1.90_2.50|edge_0_2|conf_50_55|clv_neutral",
            "market_key": "BTTS",
            "league_id": None,
            "odds_bucket": "odds_1.90_2.50",
            "confidence_bucket": "conf_50_55",
            "edge_bucket": "edge_0_2",
            "clv_bucket": "clv_neutral",
            "result_status": "win",
            "profit": 0.5,
            "clv_percent": 0.5,
            "beat_closing_line": True,
            "learning_label": "positive",
            "metadata": {},
        }
        for i in range(5)
    ]
    profiles_insuf = compute_strategy_profiles(annotations_insuf)
    adjs_insuf = generate_strategy_adjustments(profiles_insuf)
    has_request_sample = any(a["adjustment_type"] == "request_sample" for a in adjs_insuf)
    assert_true("request_sample adj for tiny sample", has_request_sample)
except Exception as e:
    fail("generate_strategy_adjustments", str(e))
    traceback.print_exc()


# ── Group 28: run_strategy_learning dry_run ───────────────────────────────────

section("28. run_strategy_learning dry-run (no data = graceful)")
try:
    from app.services.strategy_learning_service import run_strategy_learning
    result = run_strategy_learning(conn, days=30, dry_run=True)
    assert_true("result is dict", isinstance(result, dict))
    assert_true("dry_run=True", result.get("dry_run") is True)
    assert_true("annotations key", "annotations" in result)
    assert_true("profiles key", "profiles" in result)
    assert_true("adjustments key", "adjustments" in result)
    assert_true("elapsed_s key", "elapsed_s" in result)
    # No data available (in-memory, no Supabase) — should return 0 annotations gracefully
    assert_eq("0 annotations (no data)", result["annotations"], 0)
except Exception as e:
    fail("run_strategy_learning dry_run", str(e))
    traceback.print_exc()


# ── Group 29: Config settings ─────────────────────────────────────────────────

section("29. Config settings")
try:
    from app.core.config import settings
    assert_true("strategy_learning_enabled is bool",
                isinstance(settings.strategy_learning_enabled, bool))
    assert_true("strategy_learning_enabled=False by default",
                settings.strategy_learning_enabled is False)
    assert_true("strategy_learning_use_for_selection=False by default",
                settings.strategy_learning_use_for_selection is False)
    assert_true("strategy_learning_min_sample=50",
                settings.strategy_learning_min_sample == 50)
    assert_approx("score_promote", settings.strategy_learning_score_promote, 75.0)
    assert_approx("score_reduce", settings.strategy_learning_score_reduce, 40.0)
    assert_approx("max_penalty", settings.strategy_learning_max_penalty, 0.08)
    assert_approx("max_boost", settings.strategy_learning_max_boost, 0.05)
    assert_true("scheduler_strategy_learning_enabled=False",
                settings.scheduler_strategy_learning_enabled is False)
    assert_true("scheduler_strategy_learning_days=30",
                settings.scheduler_strategy_learning_days == 30)
    assert_eq("scheduler_strategy_learning_time default",
              settings.scheduler_strategy_learning_time, "00:30")
except Exception as e:
    fail("config settings", str(e))
    traceback.print_exc()


# ── Group 30: Value Engine adapter has strategy fields ────────────────────────

section("30. Value Engine adapter: strategy fields")
try:
    from app.services.value_engine_live_adapter import _EMPTY
    for field in ("strategy_key", "strategy_score", "strategy_recommendation",
                  "strategy_sample_size", "strategy_roi", "strategy_clv_beat_rate"):
        assert_true(f"_EMPTY has {field}", field in _EMPTY)
        assert_true(f"_EMPTY[{field}] = None", _EMPTY[field] is None)
except Exception as e:
    fail("VE adapter strategy fields", str(e))


# ── Group 31: VE adapter: _enrich_with_strategy exists ───────────────────────

section("31. VE adapter: _enrich_with_strategy method")
try:
    from app.services.value_engine_live_adapter import ValueEngineLiveAdapter
    adapter = ValueEngineLiveAdapter()
    assert_true("_enrich_with_strategy exists",
                hasattr(adapter, "_enrich_with_strategy"))
    assert_true("_enrich_with_strategy callable",
                callable(adapter._enrich_with_strategy))
except Exception as e:
    fail("VE adapter method", str(e))


# ── Group 32: Imports — all service and repo functions ────────────────────────

section("32. Service and repo imports")
try:
    from app.services.strategy_learning_service import (
        odds_bucket, edge_bucket, confidence_bucket, clv_bucket,
        roi_bucket, sample_bucket, build_strategy_key,
        _learning_label, _stability_score, _compute_strategy_score,
        _compute_recommendation, annotate_picks, compute_strategy_profiles,
        generate_strategy_adjustments, run_strategy_learning, get_strategy_for_pick,
    )
    ok("strategy_learning_service imports OK")

    from app.data.local.strategy_learning_repo import (
        upsert_strategy_profile, upsert_strategy_learning_run,
        upsert_strategy_adjustment, upsert_pick_learning_annotation,
        get_strategy_profiles, get_best_strategies, get_weak_strategies,
        get_strategy_by_key, get_strategy_adjustments,
        get_learning_summary, audit_strategy_learning,
    )
    ok("strategy_learning_repo imports OK")
except Exception as e:
    fail("imports", str(e))
    traceback.print_exc()


# ── Group 33: Handler syntax check ───────────────────────────────────────────

section("33. estrategias handler syntax")
try:
    import ast, pathlib
    handler_path = pathlib.Path(__file__).parent.parent / "app" / "bot" / "handlers" / "estrategias.py"
    if handler_path.exists():
        src = handler_path.read_text(encoding="utf-8")
        ast.parse(src)
        ok("estrategias.py syntax OK")
    else:
        fail("estrategias.py not found", str(handler_path))
except SyntaxError as e:
    fail("estrategias.py syntax error", str(e))
except Exception as e:
    fail("estrategias.py check", str(e))


# ── Group 34: CLI scripts syntax ─────────────────────────────────────────────

section("34. CLI scripts syntax")
try:
    import ast, pathlib
    scripts_dir = pathlib.Path(__file__).parent

    for script_name in ("build_strategy_learning.py", "audit_strategy_learning.py",
                        "report_strategies.py", "report_daily_edge.py"):
        script_path = scripts_dir / script_name
        if script_path.exists():
            src = script_path.read_text(encoding="utf-8")
            try:
                ast.parse(src)
                ok(f"{script_name} syntax OK")
            except SyntaxError as e:
                fail(f"{script_name} syntax error", str(e))
        else:
            fail(f"{script_name} not found", str(script_path))
except Exception as e:
    fail("CLI scripts syntax", str(e))


# ── Group 35: main.py imports estrategias ────────────────────────────────────

section("35. main.py imports estrategias_handler")
try:
    import pathlib
    main_path = pathlib.Path(__file__).parent.parent / "app" / "main.py"
    src = main_path.read_text(encoding="utf-8")
    assert_true("estrategias import in main.py", "estrategias" in src)
    assert_true("CommandHandler estrategias", "estrategias" in src and "CommandHandler" in src)
except Exception as e:
    fail("main.py estrategias check", str(e))


# ── Group 36: AI Router — Phase 13 intents registered ────────────────────────

section("36. AI Router: Phase 13 intents")
try:
    from app.services.ai_router_service import _VALID_INTENTS
    for intent in ("strategy_summary", "best_strategies", "weak_strategies",
                   "strategy_detail", "why_pick_strategy", "strategy_learning_status"):
        assert_true(f"intent {intent} registered", intent in _VALID_INTENTS)
except Exception as e:
    fail("AI Router intents", str(e))


# ── Group 37: AI Router — strategy phrases classified ─────────────────────────

section("37. AI Router: strategy phrases")
try:
    from app.services.ai_router_service import classify_message
    phrases = [
        ("que estrategias estan funcionando mejor", "best_strategies"),
        ("que ligas dan mejor roi", "best_strategies"),
        ("que estrategias deberia evitar", "weak_strategies"),
        ("estrategias debiles del bot", "weak_strategies"),
        ("como va el aprendizaje del bot", "strategy_learning_status"),
        ("estrategias del sistema", "strategy_summary"),
    ]
    for phrase, expected_intent in phrases:
        try:
            result = classify_message(phrase)
            got = result.get("intent")
            if got == expected_intent:
                ok(f'"{phrase[:35]}" -> {expected_intent}')
            else:
                fail(f'"{phrase[:35]}"', f"got {got!r} expected {expected_intent!r}")
        except Exception as e:
            fail(f'"{phrase[:35]}"', str(e))
except Exception as e:
    fail("AI Router strategy phrases", str(e))
    traceback.print_exc()


# ── Group 38: Scheduler settings present ──────────────────────────────────────

section("38. Scheduler: strategy_learning_job in scheduler_service")
try:
    import pathlib
    sched_path = pathlib.Path(__file__).parent.parent / "app" / "services" / "scheduler_service.py"
    src = sched_path.read_text(encoding="utf-8")
    assert_true("strategy_learning_job import reference", "strategy_learning" in src)
except Exception as e:
    fail("scheduler_service strategy ref", str(e))


# ── Group 39: scheduled_jobs.py has strategy_learning_job ─────────────────────

section("39. scheduled_jobs.py: strategy_learning_job")
try:
    import pathlib
    jobs_path = pathlib.Path(__file__).parent.parent / "app" / "services" / "scheduled_jobs.py"
    src = jobs_path.read_text(encoding="utf-8")
    assert_true("strategy_learning_job defined", "strategy_learning_job" in src)
except Exception as e:
    fail("scheduled_jobs strategy_learning_job", str(e))


# ── Group 40: run.py does not fail ───────────────────────────────────────────

section("40. run.py import check")
try:
    import pathlib, ast
    run_path = pathlib.Path(__file__).parent.parent / "run.py"
    if run_path.exists():
        src = run_path.read_text(encoding="utf-8")
        ast.parse(src)
        ok("run.py syntax OK")
    else:
        ok("run.py not found (skipped)")
except SyntaxError as e:
    fail("run.py syntax", str(e))
except Exception as e:
    fail("run.py check", str(e))


# ── Group 41: init_local_db EXPECTED_TABLES ───────────────────────────────────

section("41. init_local_db.py: Phase 13 tables")
try:
    import pathlib
    init_path = pathlib.Path(__file__).parent / "init_local_db.py"
    src = init_path.read_text(encoding="utf-8")
    for tbl in ("strategy_profiles", "strategy_learning_runs",
                "strategy_adjustments", "pick_learning_annotations"):
        assert_true(f"{tbl} in EXPECTED_TABLES", tbl in src)
except Exception as e:
    fail("init_local_db check", str(e))


# ── Group 42: valor.py has strategy section ───────────────────────────────────

section("42. valor.py: strategy learning section")
try:
    import pathlib
    valor_path = pathlib.Path(__file__).parent.parent / "app" / "bot" / "handlers" / "valor.py"
    src = valor_path.read_text(encoding="utf-8")
    assert_true("strategy reference in valor.py", "strategy" in src.lower())
except Exception as e:
    fail("valor.py strategy section", str(e))


# ── Final report ──────────────────────────────────────────────────────────────

print(f"\n{'=' * 60}")
print(f"  Resultado: {PASSED} pasados, {FAILED} fallidos")
print('=' * 60)

if ERRORS:
    print("\nFallidos:")
    for e in ERRORS:
        print(f"  {e}")
    sys.exit(1)
else:
    print("\nAll tests passed.")
    sys.exit(0)
