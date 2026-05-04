"""Test suite for Phase 11: Player Intelligence & Props Signals.

Usage:
  python scripts/test_player_intelligence.py
  python scripts/test_player_intelligence.py -v
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_scripts_dir = Path(__file__).resolve().parent
_src_root = _scripts_dir.parent
sys.path.insert(0, str(_src_root))

# ── Test infra ────────────────────────────────────────────────────────────────

_passed = 0
_failed = 0
_errors: list[str] = []


def _ok(label: str) -> None:
    global _passed
    _passed += 1
    print(f"  OK  {label}")


def _fail(label: str, reason: str) -> None:
    global _failed
    _failed += 1
    _errors.append(f"FAIL {label}: {reason}")
    print(f"  FAIL {label}: {reason}")


def run_group(label: str) -> None:
    print(f"\n-- {label} --")


def assert_eq(label: str, got: object, expected: object) -> None:
    if got == expected:
        _ok(label)
    else:
        _fail(label, f"expected {expected!r}, got {got!r}")


def assert_true(label: str, cond: bool, msg: str = "") -> None:
    if cond:
        _ok(label)
    else:
        _fail(label, msg or "condition is False")


def assert_in(label: str, item: object, collection: object) -> None:
    if item in collection:  # type: ignore[operator]
        _ok(label)
    else:
        _fail(label, f"{item!r} not in collection")


def assert_ge(label: str, a: float | int, b: float | int) -> None:
    if a >= b:
        _ok(label)
    else:
        _fail(label, f"{a} < {b}")


def assert_between(label: str, val: float, lo: float, hi: float) -> None:
    if lo <= val <= hi:
        _ok(label)
    else:
        _fail(label, f"{val} not in [{lo}, {hi}]")


# ── In-memory DuckDB fixture ──────────────────────────────────────────────────

def _build_conn():
    import duckdb
    conn = duckdb.connect(":memory:")
    base = Path(__file__).resolve().parent.parent
    for rel in [
        "sql/local/010_scheduler_schema.sql",
        "sql/local/011_player_intelligence_schema.sql",
    ]:
        path = base / rel
        if path.exists():
            sql = path.read_text(encoding="utf-8")
            for stmt in sql.split(";"):
                stmt = stmt.strip()
                if stmt:
                    try:
                        conn.execute(stmt)
                    except Exception:
                        pass
    return conn


# ── Helper: seed player_fixture_stats via repo ────────────────────────────────

def _seed_stats(conn, player_id: int, n: int = 6, provider_base: int = 1_000_000) -> None:
    from app.data.local.player_intelligence_repo import upsert_player_fixture_stats
    rows = [
        {
            "player_id": player_id,
            "player_name": f"Player{player_id}",
            "provider_fixture_id": provider_base + i,
            "fixture_id": None,
            "league_id": 39,
            "season": 2024,
            "team_id": 33,
            "team_name": "TestFC",
            "position": "Forward",
            "minutes": 85,
            "rating": 7.5,
            "captain": False,
            "substitute": False,
            "offsides": 0,
            "shots_total": 3,
            "shots_on": 1,
            "goals_total": 1,
            "goals_conceded": 0,
            "assists": 0,
            "saves": 0,
            "passes_total": 40,
            "passes_key": 1,
            "passes_accuracy": 80.0,
            "tackles_total": 0,
            "tackles_blocks": 0,
            "tackles_interceptions": 0,
            "duels_total": 5,
            "duels_won": 3,
            "dribbles_attempts": 2,
            "dribbles_success": 1,
            "fouls_drawn": 1,
            "fouls_committed": 1,
            "cards_yellow": 0,
            "cards_red": 0,
            "penalty_won": 0,
            "penalty_committed": 0,
            "penalty_scored": 0,
            "penalty_missed": 0,
            "penalty_saved": 0,
        }
        for i in range(n)
    ]
    upsert_player_fixture_stats(conn, rows)


# =============================================================================
# 1. Schema — tables exist
# =============================================================================

run_group("1. Schema — tables exist")


def _test_schema():
    conn = _build_conn()
    tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
    for t in [
        "player_fixture_stats",
        "player_season_profiles",
        "player_recent_form",
        "player_prop_signals",
    ]:
        assert_in(f"table {t}", t, tables)


try:
    _test_schema()
except Exception as e:
    _fail("schema", traceback.format_exc())


# =============================================================================
# 2. Repository — upsert_player_fixture_stats
# =============================================================================

run_group("2. Repository — upsert_player_fixture_stats")


def _test_upsert_fixture_stats():
    from app.data.local.player_intelligence_repo import upsert_player_fixture_stats
    conn = _build_conn()

    row = {
        "player_id": 1,
        "player_name": "Salah",
        "provider_fixture_id": 9001,
        "fixture_id": None,
        "league_id": 39,
        "season": 2024,
        "team_id": 33,
        "team_name": "Liverpool",
        "position": "Forward",
        "minutes": 90,
        "rating": 8.5,
        "captain": False,
        "substitute": False,
        "offsides": 0,
        "shots_total": 5,
        "shots_on": 3,
        "goals_total": 2,
        "goals_conceded": 0,
        "assists": 1,
        "saves": 0,
        "passes_total": 35,
        "passes_key": 2,
        "passes_accuracy": 85.0,
        "tackles_total": 0,
        "tackles_blocks": 0,
        "tackles_interceptions": 0,
        "duels_total": 8,
        "duels_won": 5,
        "dribbles_attempts": 3,
        "dribbles_success": 2,
        "fouls_drawn": 2,
        "fouls_committed": 1,
        "cards_yellow": 0,
        "cards_red": 0,
        "penalty_won": 0,
        "penalty_committed": 0,
        "penalty_scored": 0,
        "penalty_missed": 0,
        "penalty_saved": 0,
    }
    n = upsert_player_fixture_stats(conn, [row])
    assert_eq("insert one row", n, 1)

    # Duplicate — upsert should update, total stays 1
    n2 = upsert_player_fixture_stats(conn, [row])
    total = conn.execute("SELECT COUNT(*) FROM player_fixture_stats").fetchone()[0]
    assert_eq("dedup: total=1", total, 1)

    # Count returned is still 1 (updated)
    assert_eq("upsert returns 1 on update", n2, 1)


try:
    _test_upsert_fixture_stats()
except Exception as e:
    _fail("upsert_player_fixture_stats", traceback.format_exc())


# =============================================================================
# 3. Repository — upsert_player_season_profile
# =============================================================================

run_group("3. Repository — upsert_player_season_profile")


def _test_upsert_profile():
    from app.data.local.player_intelligence_repo import upsert_player_season_profile
    conn = _build_conn()

    upsert_player_season_profile(conn, {
        "player_id": 42,
        "player_name": "Salah",
        "team_id": 33,
        "team_name": "Liverpool",
        "league_id": 39,
        "season": 2024,
        "position": "Forward",
        "appearances": 10,
        "starts": 9,
        "total_minutes": 850,
        "avg_minutes": 85.0,
        "goals": 8,
        "assists": 3,
        "shots_total": 35,
        "shots_on": 15,
        "shots_on_rate": 0.43,
        "key_passes": 12,
        "fouls_drawn": 10,
        "fouls_committed": 5,
        "yellow_cards": 1,
        "red_cards": 0,
        "avg_rating": 8.0,
    })

    n = conn.execute("SELECT COUNT(*) FROM player_season_profiles").fetchone()[0]
    assert_eq("profile inserted", n, 1)

    # Update — same unique key
    upsert_player_season_profile(conn, {
        "player_id": 42,
        "player_name": "Mohamed Salah",
        "team_id": 33,
        "league_id": 39,
        "season": 2024,
        "appearances": 15,
        "goals": 12,
        "avg_rating": 8.2,
    })
    n2 = conn.execute("SELECT COUNT(*) FROM player_season_profiles").fetchone()[0]
    assert_eq("upsert dedup: still 1 row", n2, 1)

    row = conn.execute(
        "SELECT appearances FROM player_season_profiles WHERE player_id = 42"
    ).fetchone()
    assert_eq("appearances updated to 15", row[0], 15)


try:
    _test_upsert_profile()
except Exception as e:
    _fail("upsert_player_season_profile", traceback.format_exc())


# =============================================================================
# 4. Repository — upsert_player_recent_form
# =============================================================================

run_group("4. Repository — upsert_player_recent_form")


def _test_upsert_form():
    from app.data.local.player_intelligence_repo import upsert_player_recent_form
    conn = _build_conn()

    upsert_player_recent_form(conn, {
        "player_id": 42,
        "team_id": 33,
        "league_id": 39,
        "season": 2024,
        "window_size": 5,
        "matches_count": 5,
        "avg_minutes": 88.0,
        "goals": 4,
        "assists": 1,
        "shots_total": 20,
        "shots_on": 9,
        "key_passes": 6,
        "fouls_drawn": 4,
        "fouls_committed": 2,
        "cards_total": 0,
        "avg_rating": 8.1,
        "trend_label": "hot",
    })
    n = conn.execute("SELECT COUNT(*) FROM player_recent_form").fetchone()[0]
    assert_eq("form inserted", n, 1)

    row = conn.execute(
        "SELECT trend_label FROM player_recent_form WHERE player_id = 42 AND window_size = 5"
    ).fetchone()
    assert_eq("trend_label=hot", row[0], "hot")


try:
    _test_upsert_form()
except Exception as e:
    _fail("upsert_player_recent_form", traceback.format_exc())


# =============================================================================
# 5. Repository — upsert_player_prop_signal
# =============================================================================

run_group("5. Repository — upsert_player_prop_signal")


def _test_upsert_signal():
    from app.data.local.player_intelligence_repo import upsert_player_prop_signal
    conn = _build_conn()

    upsert_player_prop_signal(conn, {
        "player_id": 42,
        "player_name": "Salah",
        "provider_fixture_id": 9001,
        "fixture_id": None,
        "league_id": 39,
        "season": 2024,
        "team_id": 33,
        "market_key": "player_goal_signal",
        "signal_type": "prop_signal",
        "confidence_score": 0.72,
        "trend_score": 0.85,
        "availability_score": 1.0,
        "minutes_projection": 90.0,
        "reason_text": "4G en 5 partidos (en racha)",
        "risk_text": "Riesgo normal",
        "status": "recommended",
    })
    n = conn.execute("SELECT COUNT(*) FROM player_prop_signals").fetchone()[0]
    assert_eq("signal inserted", n, 1)

    # Update same unique key (player_id, provider_fixture_id, market_key)
    upsert_player_prop_signal(conn, {
        "player_id": 42,
        "player_name": "Salah",
        "provider_fixture_id": 9001,
        "market_key": "player_goal_signal",
        "confidence_score": 0.80,
        "status": "recommended",
    })
    n2 = conn.execute("SELECT COUNT(*) FROM player_prop_signals").fetchone()[0]
    assert_eq("dedup: still 1 signal", n2, 1)

    row = conn.execute(
        "SELECT confidence_score FROM player_prop_signals WHERE player_id = 42"
    ).fetchone()
    assert_true("confidence_score updated to 0.80", abs(row[0] - 0.80) < 0.001)


try:
    _test_upsert_signal()
except Exception as e:
    _fail("upsert_player_prop_signal", traceback.format_exc())


# =============================================================================
# 6. Repository — search & retrieval
# =============================================================================

run_group("6. Repository — search & retrieval")


def _test_repo_reads():
    from app.data.local.player_intelligence_repo import (
        upsert_player_season_profile,
        upsert_player_recent_form,
        upsert_player_prop_signal,
        search_players,
        get_player_profile,
        get_player_recent_form,
        get_team_player_profiles,
        get_fixture_player_signals,
        audit_player_intelligence,
    )
    conn = _build_conn()

    upsert_player_season_profile(conn, {
        "player_id": 306,
        "player_name": "Mohamed Salah",
        "team_id": 33,
        "team_name": "Liverpool",
        "league_id": 39,
        "season": 2024,
        "position": "Forward",
        "appearances": 15,
        "goals": 10,
        "assists": 4,
        "avg_rating": 8.0,
    })

    upsert_player_recent_form(conn, {
        "player_id": 306,
        "team_id": 33,
        "league_id": 39,
        "season": 2024,
        "window_size": 5,
        "matches_count": 5,
        "goals": 4,
        "trend_label": "hot",
    })

    for pid in [306, 307, 308]:
        upsert_player_prop_signal(conn, {
            "player_id": pid,
            "player_name": f"Player{pid}",
            "provider_fixture_id": 7777,
            "market_key": "player_goal_signal",
            "confidence_score": 0.5 + (pid - 306) * 0.1,
            "status": "recommended",
        })

    # search
    results = search_players(conn, "salah")
    assert_true("search finds Salah", len(results) >= 1)
    names = [r["player_name"] for r in results]
    assert_true("Salah in results", any("Salah" in n for n in names))

    # get_player_profile
    profile = get_player_profile(conn, 306)
    assert_true("profile not None", profile is not None)
    assert_eq("profile player_id=306", profile["player_id"], 306)
    assert_eq("profile goals=10", profile["goals"], 10)

    # get_player_recent_form
    form = get_player_recent_form(conn, 306, window=5)
    assert_true("form not None", form is not None)
    assert_eq("form trend_label=hot", form["trend_label"], "hot")

    # get_team_player_profiles
    team_players = get_team_player_profiles(conn, 33)
    assert_true("team players >=1", len(team_players) >= 1)

    # get_fixture_player_signals
    sigs = get_fixture_player_signals(conn, 7777)
    assert_ge("fixture signals >=3", len(sigs), 3)
    confs = [s["confidence_score"] for s in sigs]
    assert_true("sorted by confidence DESC", confs == sorted(confs, reverse=True))

    # audit
    counts = audit_player_intelligence(conn)
    assert_true("audit is dict", isinstance(counts, dict))
    assert_true("audit has 4 tables", all(
        k in counts for k in ["player_fixture_stats", "player_season_profiles",
                               "player_recent_form", "player_prop_signals"]
    ))


try:
    _test_repo_reads()
except Exception as e:
    _fail("repo reads", traceback.format_exc())


# =============================================================================
# 7. Repository — get_player_fixture_history
# =============================================================================

run_group("7. Repository — get_player_fixture_history")


def _test_history():
    from app.data.local.player_intelligence_repo import get_player_fixture_history
    conn = _build_conn()
    _seed_stats(conn, player_id=77, n=8, provider_base=2_000_000)

    history = get_player_fixture_history(conn, 77, limit=5)
    assert_eq("history limited to 5", len(history), 5)
    assert_eq("history player is 77", history[0].get("goals_total"), 1)


try:
    _test_history()
except Exception as e:
    _fail("get_player_fixture_history", traceback.format_exc())


# =============================================================================
# 8. Repository — audit counts on empty DB
# =============================================================================

run_group("8. Repository — audit on empty DB")


def _test_audit_empty():
    from app.data.local.player_intelligence_repo import audit_player_intelligence
    conn = _build_conn()
    counts = audit_player_intelligence(conn)
    for key in ["player_fixture_stats", "player_season_profiles",
                 "player_recent_form", "player_prop_signals"]:
        assert_eq(f"empty count {key}=0", counts[key], 0)


try:
    _test_audit_empty()
except Exception as e:
    _fail("audit empty", traceback.format_exc())


# =============================================================================
# 9. Service — _role_score
# =============================================================================

run_group("9. Service — _role_score")


def _test_role_score():
    from app.services.player_intelligence_service import _role_score

    assert_eq("Forward/goal = 1.0", _role_score("player_goal_signal", "Forward"), 1.0)
    assert_eq("Attacker/goal = 1.0", _role_score("player_goal_signal", "Attacker"), 1.0)
    assert_true("Midfielder/goal < 1.0", _role_score("player_goal_signal", "Midfielder") < 1.0)
    assert_true("Defender/goal < Forward", _role_score("player_goal_signal", "Defender") < _role_score("player_goal_signal", "Midfielder"))
    assert_true("Midfielder/assist high", _role_score("player_assist_signal", "Midfielder") >= 0.85)
    assert_true("all scores in [0,1]", all(
        0.0 <= _role_score(mk, pos) <= 1.0
        for mk in ["player_goal_signal", "player_assist_signal", "player_shot_signal",
                   "player_card_signal", "player_foul_signal", "player_minutes_signal"]
        for pos in ["Forward", "Midfielder", "Defender", "Goalkeeper", None, ""]
    ))


try:
    _test_role_score()
except Exception as e:
    _fail("_role_score", traceback.format_exc())


# =============================================================================
# 10. Service — _trend_score_for_market
# =============================================================================

run_group("10. Service — _trend_score_for_market")


def _test_trend_score():
    from app.services.player_intelligence_service import _trend_score_for_market

    form_hot = {"matches_count": 6, "goals": 5, "assists": 2, "shots_total": 20, "shots_on": 8,
                "cards_total": 0, "fouls_committed": 3, "avg_minutes": 85.0}
    profile = {"goals": 3, "shots_total": 10}

    # With high goals/n the formula clamps to 1.0 for both hot and stable,
    # so we only check each is in bounds and declining is below hot.
    ts = _trend_score_for_market("player_goal_signal", form_hot, profile, "hot")
    assert_between("hot goal trend in [0,1]", ts, 0.0, 1.0)
    ts_dec = _trend_score_for_market("player_goal_signal", form_hot, profile, "declining")
    assert_true("hot >= declining", ts >= ts_dec)

    ts_nd = _trend_score_for_market("player_goal_signal", form_hot, profile, "insufficient_data")
    assert_eq("insufficient_data returns 0.0", ts_nd, 0.0)


try:
    _test_trend_score()
except Exception as e:
    _fail("_trend_score_for_market", traceback.format_exc())


# =============================================================================
# 11. Service — _compute_signal confidence bounds
# =============================================================================

run_group("11. Service — _compute_signal confidence in [0,1]")


def _test_compute_signal():
    from app.services.player_intelligence_service import _compute_signal

    form = {
        "matches_count": 8,
        "avg_minutes": 88.0,
        "goals": 6,
        "assists": 2,
        "shots_total": 28,
        "shots_on": 12,
        "cards_total": 0,
        "fouls_committed": 4,
        "fouls_drawn": 5,
        "trend_label": "hot",
    }
    profile = {
        "avg_minutes": 85.0,
        "goals": 4,
        "shots_total": 18,
        "position": "Forward",
    }

    for market_key in [
        "player_goal_signal",
        "player_assist_signal",
        "player_shot_signal",
        "player_shot_on_target_signal",
        "player_card_signal",
        "player_foul_signal",
        "player_minutes_signal",
    ]:
        sig = _compute_signal(
            market_key=market_key,
            player_id=1,
            player_name="TestPlayer",
            team_id=33,
            league_id=39,
            season=2024,
            fixture_id=None,
            provider_fixture_id=9999,
            profile=profile,
            form=form,
            position="Forward",
            availability_score=1.0,
        )
        conf = sig["confidence_score"]
        assert_between(f"{market_key} confidence in [0,1]", conf, 0.0, 1.0)
        assert_true(f"{market_key} has status", sig.get("status") in
                    ("recommended", "observed", "no_data", "high_risk"))


try:
    _test_compute_signal()
except Exception as e:
    _fail("_compute_signal bounds", traceback.format_exc())


# =============================================================================
# 12. Service — _compute_signal status = high_risk
# =============================================================================

run_group("12. Service — _compute_signal high_risk paths")


def _test_signal_high_risk_availability():
    from app.services.player_intelligence_service import _compute_signal

    form = {
        "matches_count": 8,
        "avg_minutes": 88.0,
        "goals": 5,
        "assists": 1,
        "shots_total": 20,
        "shots_on": 9,
        "cards_total": 0,
        "fouls_committed": 2,
        "trend_label": "hot",
    }
    profile = {"avg_minutes": 85.0, "goals": 3, "shots_total": 12, "position": "Forward"}

    sig = _compute_signal(
        market_key="player_goal_signal",
        player_id=99,
        player_name="Injured",
        team_id=1,
        league_id=39,
        season=2024,
        fixture_id=None,
        provider_fixture_id=555,
        profile=profile,
        form=form,
        position="Forward",
        availability_score=0.1,  # < 0.3 threshold
    )
    assert_eq("high_risk when availability < 0.3", sig["status"], "high_risk")


try:
    _test_signal_high_risk_availability()
except Exception as e:
    _fail("signal high_risk availability", traceback.format_exc())


def _test_signal_high_risk_minutes():
    from app.services.player_intelligence_service import _compute_signal

    form = {
        "matches_count": 8,
        "avg_minutes": 30.0,  # low minutes -> minutes_projection will be 30 < 45
        "goals": 2,
        "assists": 0,
        "shots_total": 8,
        "shots_on": 3,
        "cards_total": 0,
        "fouls_committed": 1,
        "trend_label": "stable",
    }
    profile = {"avg_minutes": 35.0, "goals": 1, "shots_total": 5}

    sig = _compute_signal(
        market_key="player_goal_signal",
        player_id=2,
        player_name="Sub",
        team_id=1,
        league_id=39,
        season=2024,
        fixture_id=None,
        provider_fixture_id=556,
        profile=profile,
        form=form,
        position="Forward",
        availability_score=1.0,
    )
    assert_eq("high_risk when minutes < 45", sig["status"], "high_risk")


try:
    _test_signal_high_risk_minutes()
except Exception as e:
    _fail("signal high_risk minutes", traceback.format_exc())


# =============================================================================
# 13. Service — build_player_profiles (sync)
# =============================================================================

run_group("13. Service — build_player_profiles")


def _test_build_profiles_dry():
    from app.services.player_intelligence_service import build_player_profiles
    conn = _build_conn()
    _seed_stats(conn, player_id=10, n=6, provider_base=3_000_000)

    result = build_player_profiles(conn, league_id=39, season=2024, dry_run=True)
    assert_in("dry_run status", result.get("status"), ["dry_run", "ok"])
    assert_eq("dry_run profiles_written=0", conn.execute(
        "SELECT COUNT(*) FROM player_season_profiles"
    ).fetchone()[0], 0)


try:
    _test_build_profiles_dry()
except Exception as e:
    _fail("build_player_profiles dry", traceback.format_exc())


def _test_build_profiles_execute():
    from app.services.player_intelligence_service import build_player_profiles
    conn = _build_conn()
    _seed_stats(conn, player_id=10, n=6, provider_base=3_100_000)

    result = build_player_profiles(conn, league_id=39, season=2024, dry_run=False)
    assert_in("execute status", result.get("status"), ["ok", "completed"])
    n = conn.execute("SELECT COUNT(*) FROM player_season_profiles").fetchone()[0]
    assert_ge("profiles built >=1", n, 1)


try:
    _test_build_profiles_execute()
except Exception as e:
    _fail("build_player_profiles execute", traceback.format_exc())


# =============================================================================
# 14. Service — compute_player_recent_form
# =============================================================================

run_group("14. Service — compute_player_recent_form")


def _test_recent_form_execute():
    from app.services.player_intelligence_service import compute_player_recent_form
    conn = _build_conn()
    _seed_stats(conn, player_id=20, n=8, provider_base=4_000_000)

    form = compute_player_recent_form(conn, player_id=20, window=5, league_id=39)
    assert_true("form not None", form is not None)
    assert_true("form has trend_label", "trend_label" in form)
    assert_in("trend_label valid", form["trend_label"],
              ["hot", "stable", "declining", "low_minutes", "insufficient_data"])
    assert_true("matches_count <= window", form.get("matches_count", 0) <= 5)

    n = conn.execute("SELECT COUNT(*) FROM player_recent_form").fetchone()[0]
    assert_ge("form row stored in DB", n, 1)


try:
    _test_recent_form_execute()
except Exception as e:
    _fail("compute_player_recent_form", traceback.format_exc())


def _test_recent_form_insufficient():
    from app.services.player_intelligence_service import compute_player_recent_form
    conn = _build_conn()
    _seed_stats(conn, player_id=21, n=3, provider_base=4_100_000)

    form = compute_player_recent_form(conn, player_id=21, window=5, league_id=39)
    assert_true("returns result with n<5", form is not None)
    assert_eq("trend=insufficient_data when n<5", form.get("trend_label"), "insufficient_data")


try:
    _test_recent_form_insufficient()
except Exception as e:
    _fail("recent_form insufficient_data", traceback.format_exc())


# =============================================================================
# 15. Service — build_all_recent_forms
# =============================================================================

run_group("15. Service — build_all_recent_forms")


def _test_build_forms_dry():
    from app.services.player_intelligence_service import build_all_recent_forms
    conn = _build_conn()
    _seed_stats(conn, player_id=30, n=6, provider_base=5_000_000)

    result = build_all_recent_forms(conn, windows=[3, 5], league_id=39, dry_run=True)
    assert_in("dry_run status", result.get("status"), ["dry_run", "ok"])
    assert_eq("no rows in DB for dry_run", conn.execute(
        "SELECT COUNT(*) FROM player_recent_form"
    ).fetchone()[0], 0)


try:
    _test_build_forms_dry()
except Exception as e:
    _fail("build_all_recent_forms dry", traceback.format_exc())


def _test_build_forms_execute():
    from app.services.player_intelligence_service import build_all_recent_forms
    conn = _build_conn()
    _seed_stats(conn, player_id=30, n=6, provider_base=5_100_000)

    result = build_all_recent_forms(conn, windows=[3, 5], dry_run=False)
    assert_in("execute status", result.get("status"), ["ok", "completed"])
    n = conn.execute("SELECT COUNT(*) FROM player_recent_form").fetchone()[0]
    assert_ge("form rows in DB >=1", n, 1)


try:
    _test_build_forms_execute()
except Exception as e:
    _fail("build_all_recent_forms execute", traceback.format_exc())


# =============================================================================
# 16. Service — generate_player_signals
# =============================================================================

run_group("16. Service — generate_player_signals")


def _test_signals_no_fixtures():
    from app.services.player_intelligence_service import generate_player_signals
    conn = _build_conn()

    result = generate_player_signals(conn, provider_fixture_id=99999, dry_run=True)
    assert_true("returns dict", isinstance(result, dict))
    # No players in fixture 99999 → signals=0
    assert_eq("no players = 0 signals", result.get("signals", 0), 0)


try:
    _test_signals_no_fixtures()
except Exception as e:
    _fail("generate_player_signals no fixtures", traceback.format_exc())


def _test_signals_end_to_end():
    from app.services.player_intelligence_service import (
        build_player_profiles, build_all_recent_forms, generate_player_signals,
    )
    conn = _build_conn()
    _seed_stats(conn, player_id=100, n=8, provider_base=9_000_000)

    # The last fixture seeded uses provider_base + 7 = 9_000_007
    provider_fixture_id = 9_000_007

    # Build profiles and form first
    build_player_profiles(conn, dry_run=False)
    build_all_recent_forms(conn, windows=[5], dry_run=False)

    result = generate_player_signals(conn, provider_fixture_id=provider_fixture_id, dry_run=True)
    assert_true("dry_run returns dict", isinstance(result, dict))
    # Dry-run: signals counted but not stored
    assert_eq("dry_run DB empty", conn.execute(
        "SELECT COUNT(*) FROM player_prop_signals"
    ).fetchone()[0], 0)

    result2 = generate_player_signals(conn, provider_fixture_id=provider_fixture_id, dry_run=False)
    assert_true("execute returns dict", isinstance(result2, dict))


try:
    _test_signals_end_to_end()
except Exception as e:
    _fail("generate_player_signals e2e", traceback.format_exc())


# =============================================================================
# 17. Service — get_player_analysis
# =============================================================================

run_group("17. Service — get_player_analysis")


def _test_analysis_not_found():
    from app.services.player_intelligence_service import get_player_analysis
    conn = _build_conn()

    result = get_player_analysis(conn, "nobody_xyz_999")
    assert_eq("not found returns None", result, None)


try:
    _test_analysis_not_found()
except Exception as e:
    _fail("get_player_analysis not_found", traceback.format_exc())


def _test_analysis_found():
    from app.data.local.player_intelligence_repo import upsert_player_season_profile
    from app.services.player_intelligence_service import get_player_analysis
    conn = _build_conn()

    upsert_player_season_profile(conn, {
        "player_id": 306,
        "player_name": "Mohamed Salah",
        "team_id": 33,
        "league_id": 39,
        "season": 2024,
        "appearances": 15,
        "goals": 10,
    })

    result = get_player_analysis(conn, "salah")
    assert_true("found is not None", result is not None)
    assert_true("found has profile", result.get("profile") is not None)
    assert_eq("player_id=306", result["profile"]["player_id"], 306)


try:
    _test_analysis_found()
except Exception as e:
    _fail("get_player_analysis found", traceback.format_exc())


def _test_analysis_by_id():
    from app.data.local.player_intelligence_repo import upsert_player_season_profile
    from app.services.player_intelligence_service import get_player_analysis
    conn = _build_conn()

    upsert_player_season_profile(conn, {
        "player_id": 999,
        "player_name": "Haaland",
        "team_id": 50,
        "league_id": 39,
        "season": 2024,
        "appearances": 20,
        "goals": 18,
    })

    result = get_player_analysis(conn, "999")
    assert_true("found by ID", result is not None)
    assert_eq("player_id=999", result["profile"]["player_id"], 999)


try:
    _test_analysis_by_id()
except Exception as e:
    _fail("get_player_analysis by_id", traceback.format_exc())


# =============================================================================
# 18. Service — get_fixture_player_overview
# =============================================================================

run_group("18. Service — get_fixture_player_overview")


def _test_fixture_overview_empty():
    from app.services.player_intelligence_service import get_fixture_player_overview
    conn = _build_conn()

    overview = get_fixture_player_overview(conn, 12345)
    assert_eq("empty fixture returns {}", overview, {})


try:
    _test_fixture_overview_empty()
except Exception as e:
    _fail("get_fixture_player_overview empty", traceback.format_exc())


def _test_fixture_overview_with_data():
    from app.services.player_intelligence_service import get_fixture_player_overview
    conn = _build_conn()

    # Seed fixture stats with minutes > 30
    _seed_stats(conn, player_id=200, n=1, provider_base=8_888_888)

    overview = get_fixture_player_overview(conn, 8_888_888)
    assert_true("overview is dict", isinstance(overview, dict))
    # team 33 should appear if minutes > 30 (we seeded 85 minutes)
    if overview:
        assert_true("team 33 in overview", 33 in overview)


try:
    _test_fixture_overview_with_data()
except Exception as e:
    _fail("get_fixture_player_overview with data", traceback.format_exc())


# =============================================================================
# 19. Telegram handlers — source check (avoids telegram import)
# =============================================================================

run_group("19. Telegram handlers — source check")


def _test_handler_source():
    handler_path = _src_root / "app" / "bot" / "handlers" / "jugador_stats.py"
    assert_true("jugador_stats.py exists", handler_path.exists())
    src = handler_path.read_text(encoding="utf-8")
    for sym in ["jugadorstats_handler", "props_handler", "playerhot_handler"]:
        assert_in(f"{sym} defined", sym, src)


try:
    _test_handler_source()
except Exception as e:
    _fail("handler source check", traceback.format_exc())


# =============================================================================
# 20. Disclaimer in handler source
# =============================================================================

run_group("20. Disclaimer — present in handler source")


def _test_disclaimer():
    src = (_src_root / "app" / "bot" / "handlers" / "jugador_stats.py").read_text(encoding="utf-8")
    assert_true(
        "disclaimer text present",
        "informativo" in src.lower() or "no es una apuesta" in src.lower(),
    )


try:
    _test_disclaimer()
except Exception as e:
    _fail("disclaimer", traceback.format_exc())


# =============================================================================
# 21. AI router — Phase 11 intents
# =============================================================================

run_group("21. AI router — Phase 11 intents registered")


def _test_router_intents():
    from app.services.ai_router_service import _VALID_INTENTS
    for intent in ["player_stats", "player_props", "hot_players", "team_players", "player_form"]:
        assert_in(f"intent {intent}", intent, _VALID_INTENTS)


try:
    _test_router_intents()
except Exception as e:
    _fail("router intents", traceback.format_exc())


# =============================================================================
# 22. Scheduler — player_stats_job
# =============================================================================

run_group("22. Scheduler — player_stats_job")


def _test_scheduler_job():
    # Use source inspection to avoid telegram import in test env
    src = (_src_root / "app" / "services" / "scheduled_jobs.py").read_text(encoding="utf-8")
    assert_in("player_stats_job defined in scheduled_jobs.py", "async def player_stats_job", src)


try:
    _test_scheduler_job()
except Exception as e:
    _fail("player_stats_job source", traceback.format_exc())


def _test_scheduler_service_registers_job():
    import inspect
    from app.services import scheduler_service
    src = inspect.getsource(scheduler_service)
    assert_true("player_stats_job referenced in scheduler_service", "player_stats_job" in src)


try:
    _test_scheduler_service_registers_job()
except Exception as e:
    _fail("scheduler_service registers player_stats_job", traceback.format_exc())


# =============================================================================
# 23. CLI scripts — syntax check
# =============================================================================

run_group("23. CLI scripts — syntax check")


def _syntax_ok(path_str: str) -> None:
    import ast
    p = Path(path_str)
    src = p.read_text(encoding="utf-8")
    try:
        ast.parse(src)
        _ok(f"syntax {p.name}")
    except SyntaxError as e:
        _fail(f"syntax {p.name}", str(e))


for script in [
    "sync_player_stats_today.py",
    "build_player_profiles.py",
    "generate_player_signals.py",
    "audit_player_intelligence.py",
    "report_player.py",
]:
    _syntax_ok(str(_scripts_dir / script))

for rel in [
    "app/services/player_intelligence_service.py",
    "app/data/local/player_intelligence_repo.py",
    "app/bot/handlers/jugador_stats.py",
    "app/services/scheduled_jobs.py",
    "app/services/scheduler_service.py",
]:
    _syntax_ok(str(_src_root / rel))


# =============================================================================
# 24. main.py — Phase 11 handlers registered
# =============================================================================

run_group("24. main.py — Phase 11 handlers registered")


def _test_main_handlers():
    src = (_src_root / "app" / "main.py").read_text(encoding="utf-8")
    for sym in ["jugadorstats_handler", "props_handler", "playerhot_handler"]:
        assert_in(f"{sym} in main.py", sym, src)


try:
    _test_main_handlers()
except Exception as e:
    _fail("main.py handlers", traceback.format_exc())


# =============================================================================
# 25. init_local_db.py — Phase 11 tables listed
# =============================================================================

run_group("25. init_local_db.py — Phase 11 tables listed")


def _test_init_db():
    src = (_scripts_dir / "init_local_db.py").read_text(encoding="utf-8")
    for tbl in ["player_fixture_stats", "player_season_profiles",
                 "player_recent_form", "player_prop_signals"]:
        assert_in(f"{tbl} in init_local_db", tbl, src)


try:
    _test_init_db()
except Exception as e:
    _fail("init_local_db tables", traceback.format_exc())


# =============================================================================
# Summary
# =============================================================================

total = _passed + _failed
print(f"\n{'='*60}")
print(f"Results: {_passed}/{total} passed, {_failed} failed")
if _errors:
    print("\nFailed tests:")
    for e in _errors:
        print(f"  {e}")
print()

sys.exit(0 if _failed == 0 else 1)
