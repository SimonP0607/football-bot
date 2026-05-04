#!/usr/bin/env python
"""Phase 12: Market Intelligence test suite.

Tests schema, repo, service (pure computation), AI router intents,
handler source, scheduler registration, and CLI syntax.

Usage:
  python scripts/test_market_intelligence.py
  python scripts/test_market_intelligence.py -v
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_scripts_dir = Path(__file__).resolve().parent
_src_root = _scripts_dir.parent
sys.path.insert(0, str(_src_root))


# -- Test harness --------------------------------------------------------------

_PASS = 0
_FAIL = 0
_ERRORS: list[str] = []


def ok(name: str) -> None:
    global _PASS
    _PASS += 1
    print(f"  [PASS] {name}")


def fail(name: str, reason: str) -> None:
    global _FAIL
    _FAIL += 1
    _ERRORS.append(f"{name}: {reason}")
    print(f"  [FAIL] {name} -- {reason}")


def assert_eq(name: str, got, expected) -> None:
    if got == expected:
        ok(name)
    else:
        fail(name, f"got {got!r}, want {expected!r}")


def assert_true(name: str, value, msg: str = "") -> None:
    if value:
        ok(name)
    else:
        fail(name, msg or f"expected truthy, got {value!r}")


def assert_false(name: str, value, msg: str = "") -> None:
    if not value:
        ok(name)
    else:
        fail(name, msg or f"expected falsy, got {value!r}")


def assert_in(name: str, item, container) -> None:
    if item in container:
        ok(name)
    else:
        fail(name, f"{item!r} not in {container!r}")


def assert_ge(name: str, got, minimum) -> None:
    if got >= minimum:
        ok(name)
    else:
        fail(name, f"got {got!r} < {minimum!r}")


def section(title: str) -> None:
    print(f"\n{'-' * 60}")
    print(f"  {title}")
    print(f"{'-' * 60}")


# -- In-memory DuckDB fixture ---------------------------------------------------


def _make_conn():
    import duckdb
    conn = duckdb.connect(":memory:")
    # Apply scheduler schema (needed for init) and market schema
    schema_dir = _src_root / "sql" / "local"
    for sql_file in sorted(schema_dir.glob("*.sql")):
        try:
            sql = sql_file.read_text(encoding="utf-8")
            conn.execute(sql)
        except Exception as exc:
            pass  # some schemas may require other tables first
    return conn


def _seed_snapshot(conn, provider_fixture_id: int, market_key: str, selection: str,
                   odds: float, snapshot_type: str = "prematch", bk_id: int = 8) -> None:
    from app.data.local.market_intelligence_repo import upsert_odds_snapshot
    upsert_odds_snapshot(conn, [{
        "provider_fixture_id": provider_fixture_id,
        "fixture_id": None,
        "league_id": 39,
        "season": 2025,
        "bookmaker_id": bk_id,
        "bookmaker_name": "Bet365",
        "market_key": market_key,
        "selection": selection,
        "odds": odds,
        "implied_probability": round(1.0 / odds, 6),
        "snapshot_type": snapshot_type,
        "snapshot_time": "2025-05-03T10:00:00",
        "minutes_to_kickoff": 120.0,
        "source": "api_football",
    }])


# -- Group 1: Schema tables ----------------------------------------------------

def test_schema_tables() -> None:
    section("1. Schema -- tablas presentes")
    conn = _make_conn()
    for table in [
        "market_odds_history",
        "market_closing_lines",
        "pick_clv_results",
        "market_movement_signals",
    ]:
        try:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert_eq(f"tabla {table} existe y es vacia", cnt, 0)
        except Exception as exc:
            fail(f"tabla {table}", str(exc))
    conn.close()


# -- Group 2: Sequences --------------------------------------------------------

def test_sequences() -> None:
    section("2. Sequences")
    conn = _make_conn()
    for seq in [
        "market_odds_history_seq",
        "market_closing_lines_seq",
        "pick_clv_results_seq",
        "market_movement_signals_seq",
    ]:
        try:
            val = conn.execute(f"SELECT nextval('{seq}')").fetchone()[0]
            assert_true(f"sequence {seq} avanza", val is not None and val >= 1)
        except Exception as exc:
            fail(f"sequence {seq}", str(exc))
    conn.close()


# -- Group 3: upsert_odds_snapshot --------------------------------------------

def test_upsert_odds_snapshot() -> None:
    section("3. upsert_odds_snapshot")
    conn = _make_conn()
    from app.data.local.market_intelligence_repo import upsert_odds_snapshot

    rows = [
        {
            "provider_fixture_id": 1000001,
            "fixture_id": None,
            "league_id": 39,
            "season": 2025,
            "bookmaker_id": 8,
            "bookmaker_name": "Bet365",
            "market_key": "Match Winner",
            "selection": "Home",
            "odds": 2.10,
            "implied_probability": 0.476,
            "snapshot_type": "prematch",
            "snapshot_time": "2025-05-03T10:00:00",
            "minutes_to_kickoff": 120.0,
            "source": "api_football",
        },
        {
            "provider_fixture_id": 1000001,
            "league_id": 39,
            "season": 2025,
            "bookmaker_id": 8,
            "bookmaker_name": "Bet365",
            "market_key": "Match Winner",
            "selection": "Away",
            "odds": 3.50,
            "implied_probability": 0.286,
            "snapshot_type": "prematch",
            "snapshot_time": "2025-05-03T10:00:00",
        },
    ]
    inserted = upsert_odds_snapshot(conn, rows)
    assert_eq("upsert 2 filas retorna 2", inserted, 2)

    cnt = conn.execute("SELECT COUNT(*) FROM market_odds_history").fetchone()[0]
    assert_eq("2 filas en market_odds_history", cnt, 2)

    # Empty list
    assert_eq("upsert vacio retorna 0", upsert_odds_snapshot(conn, []), 0)
    conn.close()


# -- Group 4: get_odds_history -------------------------------------------------

def test_get_odds_history() -> None:
    section("4. get_odds_history")
    conn = _make_conn()
    from app.data.local.market_intelligence_repo import get_odds_history

    # Empty
    result = get_odds_history(conn, 9999999)
    assert_eq("get_odds_history sin datos retorna []", result, [])

    # Seed and fetch
    _seed_snapshot(conn, 2000001, "Match Winner", "Home", 2.10)
    _seed_snapshot(conn, 2000001, "Match Winner", "Away", 3.50)
    _seed_snapshot(conn, 2000001, "Goals Over/Under", "Over 2.5", 1.85)

    result = get_odds_history(conn, 2000001)
    assert_ge("3 snapshots para fixture 2000001", len(result), 3)

    # Filter by market_key
    result_mk = get_odds_history(conn, 2000001, market_key="Match Winner")
    assert_ge("filtra por market_key", len(result_mk), 2)

    # Filter by selection
    result_sel = get_odds_history(conn, 2000001, selection="Home")
    assert_ge("filtra por selection", len(result_sel), 1)
    conn.close()


# -- Group 5: get_opening_odds -------------------------------------------------

def test_get_opening_odds() -> None:
    section("5. get_opening_odds")
    conn = _make_conn()
    from app.data.local.market_intelligence_repo import get_opening_odds

    assert_eq("opening odds vacio retorna None", get_opening_odds(conn, 9999, "M", "H"), None)

    _seed_snapshot(conn, 3000001, "Match Winner", "Home", 2.00)
    result = get_opening_odds(conn, 3000001, "Match Winner", "Home")
    assert_true("opening odds retorna dict", result is not None)
    assert_eq("opening odds correctos", result["odds"], 2.00)
    conn.close()


# -- Group 6: upsert_closing_line ----------------------------------------------

def test_upsert_closing_line() -> None:
    section("6. upsert_closing_line")
    conn = _make_conn()
    from app.data.local.market_intelligence_repo import upsert_closing_line, get_closing_line

    row = {
        "provider_fixture_id": 4000001,
        "fixture_id": None,
        "league_id": 39,
        "season": 2025,
        "bookmaker_id": 8,
        "bookmaker_name": "Bet365",
        "market_key": "Match Winner",
        "selection": "Home",
        "opening_odds": 2.20,
        "best_seen_odds": 2.30,
        "closing_odds": 2.00,
        "opening_implied": 0.4545,
        "closing_implied": 0.5000,
        "odds_delta": -0.20,
        "implied_delta": 0.0455,
        "movement_label": "steam_towards_selection",
        "confidence_score": 0.8,
    }

    result = upsert_closing_line(conn, row)
    assert_true("upsert_closing_line retorna True", result)

    cnt = conn.execute("SELECT COUNT(*) FROM market_closing_lines").fetchone()[0]
    assert_eq("1 fila en market_closing_lines", cnt, 1)

    # Verify data
    cl = get_closing_line(conn, 4000001, "Match Winner", "Home", 8)
    assert_true("get_closing_line retorna dict", cl is not None)
    assert_true("opening_odds correcto", cl["opening_odds"] is not None and abs(cl["opening_odds"] - 2.20) < 0.01)
    assert_eq("movement_label correcto", cl["movement_label"], "steam_towards_selection")

    # Upsert again (ON CONFLICT update)
    row2 = dict(row)
    row2["closing_odds"] = 1.95
    result2 = upsert_closing_line(conn, row2)
    assert_true("segundo upsert retorna True", result2)

    cnt2 = conn.execute("SELECT COUNT(*) FROM market_closing_lines").fetchone()[0]
    assert_eq("sigue siendo 1 fila despues del upsert", cnt2, 1)
    conn.close()


# -- Group 7: get_closing_lines_for_fixture ------------------------------------

def test_get_closing_lines_for_fixture() -> None:
    section("7. get_closing_lines_for_fixture")
    conn = _make_conn()
    from app.data.local.market_intelligence_repo import upsert_closing_line, get_closing_lines_for_fixture

    # Empty
    assert_eq("sin datos retorna []", get_closing_lines_for_fixture(conn, 9999), [])

    # Seed 3 selections
    for sel, opening, closing in [("Home", 2.20, 2.00), ("Draw", 3.40, 3.50), ("Away", 3.60, 3.80)]:
        upsert_closing_line(conn, {
            "provider_fixture_id": 5000001,
            "bookmaker_id": 8,
            "market_key": "Match Winner",
            "selection": sel,
            "opening_odds": opening,
            "closing_odds": closing,
            "movement_label": "stable",
        })

    result = get_closing_lines_for_fixture(conn, 5000001)
    assert_eq("3 closing lines para fixture", len(result), 3)
    conn.close()


# -- Group 8: upsert_pick_clv -------------------------------------------------

def test_upsert_pick_clv() -> None:
    section("8. upsert_pick_clv")
    conn = _make_conn()
    from app.data.local.market_intelligence_repo import upsert_pick_clv, get_pick_clv

    row = {
        "pick_candidate_id": 100,
        "published_pick_id": 10,
        "fixture_id": None,
        "provider_fixture_id": 6000001,
        "league_id": 39,
        "market_key": "Match Winner",
        "selection": "Home",
        "pick_odds": 2.20,
        "closing_odds": 2.00,
        "pick_implied": 0.4545,
        "closing_implied": 0.5000,
        "clv_percent": 4.55,
        "clv_result": "positive",
        "beat_closing_line": True,
        "bookmaker_name": "Bet365",
    }
    result = upsert_pick_clv(conn, row)
    assert_true("upsert_pick_clv retorna True", result)

    records = get_pick_clv(conn, 100)
    assert_eq("1 registro CLV para pick 100", len(records), 1)
    assert_eq("clv_result correcto", records[0]["clv_result"], "positive")
    assert_true("beat_closing_line True", records[0]["beat_closing_line"])

    # Conflict -- update
    row2 = dict(row)
    row2["clv_result"] = "neutral"
    upsert_pick_clv(conn, row2)
    cnt = conn.execute("SELECT COUNT(*) FROM pick_clv_results").fetchone()[0]
    assert_eq("sigue 1 fila despues del upsert", cnt, 1)
    conn.close()


# -- Group 9: get_clv_summary -------------------------------------------------

def test_get_clv_summary() -> None:
    section("9. get_clv_summary")
    conn = _make_conn()
    from app.data.local.market_intelligence_repo import upsert_pick_clv, get_clv_summary

    # Empty
    result = get_clv_summary(conn, days=30)
    assert_eq("clv_summary vacio: total=0", result.get("total", 0), 0)

    # Seed 3 picks
    for pid, clv, result_label, beat in [
        (201, 5.0, "positive", True),
        (202, 0.1, "neutral", False),
        (203, -3.0, "negative", False),
    ]:
        upsert_pick_clv(conn, {
            "pick_candidate_id": pid,
            "market_key": "Match Winner",
            "selection": "Home",
            "provider_fixture_id": 7000001,
            "pick_odds": 2.0,
            "closing_odds": 1.9 if beat else 2.1,
            "clv_percent": clv,
            "clv_result": result_label,
            "beat_closing_line": beat,
        })

    summary = get_clv_summary(conn, days=30)
    assert_eq("total = 3", summary.get("total"), 3)
    assert_eq("beat_count = 1", summary.get("beat_count"), 1)
    assert_eq("positive_count = 1", summary.get("positive_count"), 1)
    assert_eq("negative_count = 1", summary.get("negative_count"), 1)
    conn.close()


# -- Group 10: upsert_market_signal -------------------------------------------

def test_upsert_market_signal() -> None:
    section("10. upsert_market_signal")
    conn = _make_conn()
    from app.data.local.market_intelligence_repo import upsert_market_signal, get_market_signals

    row = {
        "provider_fixture_id": 8000001,
        "market_key": "Match Winner",
        "selection": "Home",
        "signal_type": "steam",
        "movement_label": "steam_towards_selection",
        "opening_odds": 2.40,
        "current_odds": 2.00,
        "implied_delta": 0.083,
        "confidence_score": 0.7,
        "reason_text": "Cuota bajo de 2.40 a 2.00",
        "risk_text": "Steam fuerte",
        "status": "observed",
    }
    result = upsert_market_signal(conn, row)
    assert_true("upsert_market_signal retorna True", result)

    signals = get_market_signals(conn, provider_fixture_id=8000001)
    assert_eq("1 senal para fixture 8000001", len(signals), 1)
    assert_eq("signal_type correcto", signals[0]["signal_type"], "steam")

    # Conflict upsert
    row2 = dict(row)
    row2["current_odds"] = 1.95
    upsert_market_signal(conn, row2)
    cnt = conn.execute("SELECT COUNT(*) FROM market_movement_signals").fetchone()[0]
    assert_eq("sigue 1 senal despues del upsert", cnt, 1)
    conn.close()


# -- Group 11: get_market_intelligence_summary ---------------------------------

def test_get_market_intelligence_summary() -> None:
    section("11. get_market_intelligence_summary")
    conn = _make_conn()
    from app.data.local.market_intelligence_repo import (
        upsert_closing_line, upsert_market_signal,
        get_market_intelligence_summary,
    )

    # Empty
    assert_eq("summary vacio retorna {}", get_market_intelligence_summary(conn, 9999999), {})

    pfid = 9000001
    upsert_closing_line(conn, {
        "provider_fixture_id": pfid, "bookmaker_id": 8,
        "market_key": "Match Winner", "selection": "Home",
        "opening_odds": 2.20, "closing_odds": 2.00,
        "movement_label": "steam_towards_selection",
    })
    upsert_market_signal(conn, {
        "provider_fixture_id": pfid, "market_key": "Match Winner",
        "selection": "Home", "signal_type": "steam",
        "movement_label": "steam_towards_selection",
        "implied_delta": 0.05,
    })

    summary = get_market_intelligence_summary(conn, pfid)
    assert_true("summary retorna dict no vacio", bool(summary))
    assert_true("has_steam True", summary.get("has_steam"))
    assert_ge("tiene closing lines", len(summary.get("closing_lines", [])), 1)
    conn.close()


# -- Group 12: get_market_stats ------------------------------------------------

def test_get_market_stats() -> None:
    section("12. get_market_stats")
    conn = _make_conn()
    from app.data.local.market_intelligence_repo import get_market_stats

    stats = get_market_stats(conn)
    assert_true("stats es dict", isinstance(stats, dict))
    for key in ["odds_history_count", "closing_lines_count", "pick_clv_count", "signals_count"]:
        assert_true(f"stats tiene clave '{key}'", key in stats)
    assert_eq("odds_history_count=0 en DB vacia", stats["odds_history_count"], 0)
    conn.close()


# -- Group 13: Service -- _implied ----------------------------------------------

def test_service_implied() -> None:
    section("13. Service -- _implied()")
    from app.services.market_intelligence_service import _implied

    assert_eq("_implied(2.0) = 0.5", _implied(2.0), 0.5)
    assert_eq("_implied(None) = None", _implied(None), None)
    assert_eq("_implied(1.0) = None (odds <= 1)", _implied(1.0), None)
    assert_eq("_implied(0.5) = None (odds <= 1)", _implied(0.5), None)

    result = _implied(4.0)
    assert_true("_implied(4.0) ~= 0.25", result is not None and abs(result - 0.25) < 0.001)


# -- Group 14: Service -- _movement_label --------------------------------------

def test_service_movement_label() -> None:
    section("14. Service -- _movement_label()")
    from app.services.market_intelligence_service import _movement_label

    min_mv = 0.025
    assert_eq("delta=None -> no_data", _movement_label(None, min_mv), "no_data")
    assert_eq("delta=0.001 (< min) -> stable", _movement_label(0.001, min_mv), "stable")
    assert_eq("delta=0.05 > 0 -> steam_towards_selection", _movement_label(0.05, min_mv), "steam_towards_selection")
    assert_eq("delta=-0.05 < 0 -> drift_against_selection", _movement_label(-0.05, min_mv), "drift_against_selection")


# -- Group 15: Service -- _clv_result ------------------------------------------

def test_service_clv_result() -> None:
    section("15. Service -- _clv_result()")
    from app.services.market_intelligence_service import _clv_result

    nb = 0.005  # neutral_band
    assert_eq("None -> no_data", _clv_result(None, nb), "no_data")
    assert_eq("clv=0.0 -> neutral", _clv_result(0.0, nb), "neutral")
    assert_eq("clv=0.001 (<= threshold) -> neutral", _clv_result(0.001, nb), "neutral")  # 0.001 <= 0.5pp
    assert_eq("clv=2.0 -> positive", _clv_result(2.0, nb), "positive")
    assert_eq("clv=-2.0 -> negative", _clv_result(-2.0, nb), "negative")


# -- Group 16: Service -- _detect_signal_type ----------------------------------

def test_service_detect_signal_type() -> None:
    section("16. Service -- _detect_signal_type()")
    from app.services.market_intelligence_service import _detect_signal_type

    min_mv = 0.025
    assert_eq("no delta -> stable", _detect_signal_type(None, min_mv, 5), "stable")
    assert_eq("1 snapshot -> stable", _detect_signal_type(0.1, min_mv, 1), "stable")
    assert_eq("below threshold -> stable", _detect_signal_type(0.01, min_mv, 5), "stable")

    # Large positive delta = steam
    signal = _detect_signal_type(0.1, min_mv, 5)
    assert_in("large positive ? is steam or sharp_move", signal, ("steam", "sharp_move"))

    # Negative delta = drift
    assert_eq("negative delta = drift", _detect_signal_type(-0.05, min_mv, 5), "drift")


# -- Group 17: Service -- build_closing_lines (sync, no API) -------------------

def test_build_closing_lines() -> None:
    section("17. Service -- build_closing_lines (sync)")
    conn = _make_conn()
    from app.services.market_intelligence_service import build_closing_lines

    # Seed history: opening + closing snapshots for same market/selection
    from app.data.local.market_intelligence_repo import upsert_odds_snapshot

    upsert_odds_snapshot(conn, [
        {
            "provider_fixture_id": 10000001,
            "bookmaker_id": 8, "bookmaker_name": "Bet365",
            "market_key": "Match Winner", "selection": "Home",
            "odds": 2.40, "implied_probability": 0.4167,
            "snapshot_type": "opening",
            "snapshot_time": "2025-05-03T08:00:00",
        },
        {
            "provider_fixture_id": 10000001,
            "bookmaker_id": 8, "bookmaker_name": "Bet365",
            "market_key": "Match Winner", "selection": "Home",
            "odds": 2.10, "implied_probability": 0.4762,
            "snapshot_type": "prematch",
            "snapshot_time": "2025-05-03T14:00:00",
        },
        {
            "provider_fixture_id": 10000001,
            "bookmaker_id": 8, "bookmaker_name": "Bet365",
            "market_key": "Match Winner", "selection": "Home",
            "odds": 2.00, "implied_probability": 0.5000,
            "snapshot_type": "closing",
            "snapshot_time": "2025-05-03T15:50:00",
        },
    ])

    result = build_closing_lines(conn, [10000001], dry_run=True)
    assert_ge("dry_run: procesado>=1", result["processed"], 1)

    result2 = build_closing_lines(conn, [10000001], dry_run=False)
    assert_ge("real: procesado>=1", result2["processed"], 1)
    assert_ge("real: updated>=1", result2["updated"], 1)

    cl_count = conn.execute("SELECT COUNT(*) FROM market_closing_lines").fetchone()[0]
    assert_ge("closing line creada en DB", cl_count, 1)

    # Verify movement label (steam towards selection -- implied increased)
    cl = conn.execute(
        "SELECT movement_label, opening_odds, closing_odds FROM market_closing_lines LIMIT 1"
    ).fetchone()
    assert_true("movement_label asignado", cl is not None and cl[0] is not None)
    assert_true("opening_odds = 2.40", abs(cl[1] - 2.40) < 0.01)
    assert_true("closing_odds = 2.00", abs(cl[2] - 2.00) < 0.01)
    conn.close()


# -- Group 18: Service -- compute_pick_clv (sync) -------------------------------

def test_compute_pick_clv() -> None:
    section("18. Service -- compute_pick_clv (sync)")
    conn = _make_conn()
    from app.services.market_intelligence_service import compute_pick_clv
    from app.data.local.market_intelligence_repo import upsert_closing_line

    upsert_closing_line(conn, {
        "provider_fixture_id": 11000001,
        "bookmaker_id": 8,
        "market_key": "Match Winner",
        "selection": "Home",
        "closing_odds": 2.00,
        "closing_implied": 0.5000,
        "movement_label": "steam_towards_selection",
    })

    picks = [
        {
            "pick_candidate_id": 301,
            "provider_fixture_id": 11000001,
            "market_key": "Match Winner",
            "selection": "Home",
            "pick_odds": 2.40,  # > 2.00 closing -- beat the line
        },
        {
            "pick_candidate_id": 302,
            "provider_fixture_id": 11000001,
            "market_key": "Match Winner",
            "selection": "Home",
            "pick_odds": 1.80,  # < 2.00 closing -- lost the line
        },
        {
            "pick_candidate_id": 303,
            "provider_fixture_id": 11000099,  # no closing line
            "market_key": "Match Winner",
            "selection": "Home",
            "pick_odds": 2.10,
        },
    ]

    # Dry-run
    result = compute_pick_clv(conn, picks, dry_run=True)
    assert_eq("total = 3", result["total"], 3)
    assert_eq("computed = 2 (1 no closing line)", result["computed"], 2)
    assert_eq("no_closing_line = 1", result["no_closing_line"], 1)

    # Real run
    result2 = compute_pick_clv(conn, picks, dry_run=False)
    assert_eq("computed = 2 real", result2["computed"], 2)

    clv_count = conn.execute("SELECT COUNT(*) FROM pick_clv_results").fetchone()[0]
    assert_eq("2 CLV en DB", clv_count, 2)

    # Verify beat_closing_line logic
    beat_row = conn.execute(
        "SELECT beat_closing_line, clv_percent FROM pick_clv_results WHERE pick_candidate_id=301"
    ).fetchone()
    assert_true("pick 301: beat_closing_line=True", beat_row and beat_row[0])
    assert_true("pick 301: clv_percent > 0", beat_row and beat_row[1] > 0)

    loss_row = conn.execute(
        "SELECT beat_closing_line, clv_percent FROM pick_clv_results WHERE pick_candidate_id=302"
    ).fetchone()
    assert_false("pick 302: beat_closing_line=False", loss_row and loss_row[0])
    assert_true("pick 302: clv_percent < 0", loss_row and loss_row[1] < 0)
    conn.close()


# -- Group 19: Service -- detect_market_signals (sync) -------------------------

def test_detect_market_signals() -> None:
    section("19. Service -- detect_market_signals (sync)")
    conn = _make_conn()
    from app.services.market_intelligence_service import detect_market_signals
    from app.data.local.market_intelligence_repo import upsert_odds_snapshot

    # Seed: opening at 2.40, closing at 2.00 = big steam move
    upsert_odds_snapshot(conn, [
        {
            "provider_fixture_id": 12000001,
            "bookmaker_id": 8, "bookmaker_name": "Bet365",
            "market_key": "Match Winner", "selection": "Home",
            "odds": 2.40, "implied_probability": 0.4167,
            "snapshot_type": "opening",
            "snapshot_time": "2025-05-03T08:00:00",
        },
        {
            "provider_fixture_id": 12000001,
            "bookmaker_id": 8, "bookmaker_name": "Bet365",
            "market_key": "Match Winner", "selection": "Home",
            "odds": 2.00, "implied_probability": 0.5000,
            "snapshot_type": "closing",
            "snapshot_time": "2025-05-03T15:50:00",
        },
    ])

    result = detect_market_signals(conn, [12000001], dry_run=True)
    assert_ge("dry_run: processed>=1", result["processed"], 1)
    assert_ge("dry_run: signals_created>=1", result["signals_created"], 1)

    # Single snapshot -- should be skipped
    _seed_snapshot(conn, 12000002, "Match Winner", "Home", 2.0)
    result2 = detect_market_signals(conn, [12000002], dry_run=True)
    assert_ge("single snapshot: skipped>=1", result2["skipped"], 1)
    conn.close()


# -- Group 20: Service -- get_fixture_market_summary ---------------------------

def test_get_fixture_market_summary() -> None:
    section("20. Service -- get_fixture_market_summary")
    conn = _make_conn()
    from app.services.market_intelligence_service import get_fixture_market_summary

    # Empty
    assert_eq("empty returns {}", get_fixture_market_summary(conn, 9999999), {})

    # With data
    from app.data.local.market_intelligence_repo import upsert_closing_line
    upsert_closing_line(conn, {
        "provider_fixture_id": 13000001, "bookmaker_id": 8,
        "market_key": "Match Winner", "selection": "Home",
        "opening_odds": 2.20, "closing_odds": 2.00,
        "movement_label": "steam_towards_selection",
    })

    summary = get_fixture_market_summary(conn, 13000001)
    assert_true("retorna dict", bool(summary))
    assert_in("closing_lines en summary", "closing_lines", summary)
    conn.close()


# -- Group 21: Service -- get_clv_report ---------------------------------------

def test_get_clv_report() -> None:
    section("21. Service -- get_clv_report")
    conn = _make_conn()
    from app.services.market_intelligence_service import get_clv_report

    # Empty
    result = get_clv_report(conn, days=30)
    assert_true("retorna dict", isinstance(result, dict))

    # With data
    from app.data.local.market_intelligence_repo import upsert_pick_clv
    upsert_pick_clv(conn, {
        "pick_candidate_id": 401,
        "market_key": "Match Winner",
        "selection": "Home",
        "provider_fixture_id": 14000001,
        "pick_odds": 2.20,
        "closing_odds": 2.00,
        "clv_percent": 4.55,
        "clv_result": "positive",
        "beat_closing_line": True,
    })

    report = get_clv_report(conn, days=30)
    assert_ge("total >= 1", report.get("total", 0), 1)
    conn.close()


# -- Group 22: Service -- _parse_odds_response_item ----------------------------

def test_parse_odds_response_item() -> None:
    section("22. Service -- _parse_odds_response_item")
    from app.services.market_intelligence_service import _parse_odds_response_item

    item = {
        "fixture": {"id": 15000001},
        "league": {"id": 39, "season": 2025},
        "bookmakers": [
            {
                "id": 8,
                "name": "Bet365",
                "bets": [
                    {
                        "id": 1,
                        "name": "Match Winner",
                        "values": [
                            {"value": "Home", "odd": "2.10"},
                            {"value": "Draw", "odd": "3.40"},
                            {"value": "Away", "odd": "3.60"},
                        ],
                    }
                ],
            }
        ],
    }

    rows = _parse_odds_response_item(item, "prematch")
    assert_eq("3 rows parseados", len(rows), 3)
    assert_eq("provider_fixture_id correcto", rows[0]["provider_fixture_id"], 15000001)
    assert_eq("market_key correcto", rows[0]["market_key"], "Match Winner")
    assert_eq("odds correcto (Home)", rows[0]["odds"], 2.10)
    assert_true("implied_probability calculado", rows[0]["implied_probability"] is not None)

    # Invalid odds (<= 1.0) should be skipped
    item_bad = dict(item)
    item_bad["bookmakers"] = [{"id": 8, "name": "Bet365", "bets": [{
        "id": 1, "name": "Test", "values": [{"value": "X", "odd": "0.90"}]
    }]}]
    rows_bad = _parse_odds_response_item(item_bad, "prematch")
    assert_eq("odds <= 1.0 descartados", len(rows_bad), 0)


# -- Group 23: AI Router -- market intents --------------------------------------

def test_ai_router_market_intents() -> None:
    section("23. AI Router -- market intents (Phase 12)")
    from app.services.ai_router_service import classify_with_rules

    tests = [
        ("clv", "clv de los ultimos 30 dias", "clv_summary"),
        ("closing line value", "dame el closing line value", "clv_summary"),
        ("fixture_market with number", "mercado del partido 1035066", "fixture_market"),
        ("odds_movement steam", "hubo steam en este partido", "odds_movement"),
        ("odds_movement momios", "los momios bajaron mucho", "odds_movement"),
        ("market_summary general", "mercado de hoy", "market_summary"),
    ]
    for label, text, expected_intent in tests:
        result = classify_with_rules(text)
        intent = result.get("intent")
        assert_eq(f"'{label}' -> {expected_intent}", intent, expected_intent)

    # Valid intents list
    from app.services.ai_router_service import _VALID_INTENTS
    for intent in ["market_summary", "fixture_market", "clv_summary", "odds_movement", "bookmaker_coverage"]:
        assert_in(f"'{intent}' en _VALID_INTENTS", intent, _VALID_INTENTS)


# -- Group 24: Handler source -- mercado.py ------------------------------------

def test_handler_source() -> None:
    section("24. Handler source -- mercado.py, clv_handler")
    handler_path = _src_root / "app" / "bot" / "handlers" / "mercado.py"
    assert_true("mercado.py existe", handler_path.exists())

    src = handler_path.read_text(encoding="utf-8")
    assert_in("mercado_handler definido", "async def mercado_handler", src)
    assert_in("clv_handler definido", "async def clv_handler", src)
    assert_in("@require_auth en mercado", "@require_auth", src)
    assert_in("disclaimer presente", "_DISCLAIMER", src)


# -- Group 25: main.py -- handlers registrados ---------------------------------

def test_main_registration() -> None:
    section("25. main.py -- /mercado y /clv registrados")
    main_path = _src_root / "app" / "main.py"
    src = main_path.read_text(encoding="utf-8")

    assert_in("mercado_handler importado", "mercado_handler", src)
    assert_in("clv_handler importado", "clv_handler", src)
    assert_in("/mercado CommandHandler", 'CommandHandler("mercado"', src)
    assert_in("/clv CommandHandler", 'CommandHandler("clv"', src)
    assert_in("BotCommand mercado", 'BotCommand("mercado"', src)
    assert_in("BotCommand clv", 'BotCommand("clv"', src)


# -- Group 26: config.py -- Phase 12 settings ----------------------------------

def test_config_settings() -> None:
    section("26. config.py -- Phase 12 settings")
    from app.core.config import settings

    assert_false("market_intelligence_enabled default False", settings.market_intelligence_enabled)
    assert_false("market_intelligence_use_live_odds default False", settings.market_intelligence_use_live_odds)
    assert_eq("market_intelligence_min_movement = 0.025", settings.market_intelligence_min_movement, 0.025)
    assert_eq("market_intelligence_clv_neutral_band = 0.005", settings.market_intelligence_clv_neutral_band, 0.005)
    assert_true("bookmaker_priority is str", isinstance(settings.market_intelligence_default_bookmaker_priority, str))
    assert_eq("max_requests_per_run = 500", settings.market_intelligence_max_requests_per_run, 500)
    assert_false("scheduler_market_enabled default False", settings.scheduler_market_enabled)
    assert_eq("scheduler_market_opening_time = 08:00", settings.scheduler_market_opening_time, "08:00")
    assert_eq("scheduler_market_prematch_hours = 6", settings.scheduler_market_prematch_hours, 6)
    assert_eq("scheduler_market_closing_minutes = 15", settings.scheduler_market_closing_minutes, 15)
    assert_eq("scheduler_clv_time = 23:45", settings.scheduler_clv_time, "23:45")


# -- Group 27: init_local_db EXPECTED_TABLES ----------------------------------

def test_init_local_db_tables() -> None:
    section("27. init_local_db -- EXPECTED_TABLES Phase 12")
    init_path = _scripts_dir / "init_local_db.py"
    src = init_path.read_text(encoding="utf-8")
    for table in ["market_odds_history", "market_closing_lines", "pick_clv_results", "market_movement_signals"]:
        assert_in(f"'{table}' en EXPECTED_TABLES", f'"{table}"', src)


# -- Group 28: scheduled_jobs.py -- market jobs --------------------------------

def test_scheduled_jobs_source() -> None:
    section("28. scheduled_jobs.py -- market jobs")
    sj_path = _src_root / "app" / "services" / "scheduled_jobs.py"
    src = sj_path.read_text(encoding="utf-8")

    for fn in ["market_opening_job", "market_prematch_job", "market_closing_job", "market_clv_job"]:
        assert_in(f"'{fn}' definido", f"async def {fn}", src)


# -- Group 29: scheduler_service.py -- market jobs registrados -----------------

def test_scheduler_service_source() -> None:
    section("29. scheduler_service.py -- market jobs registrados")
    ss_path = _src_root / "app" / "services" / "scheduler_service.py"
    src = ss_path.read_text(encoding="utf-8")

    for fn in ["market_opening_job", "market_prematch_job", "market_closing_job", "market_clv_job"]:
        assert_in(f"'{fn}' importado", fn, src)
    assert_in("scheduler_market_enabled", "scheduler_market_enabled", src)


# -- Group 30: CLI syntax checks -----------------------------------------------

def test_cli_syntax() -> None:
    section("30. CLI scripts -- syntax valida")
    import ast

    for script in [
        "sync_market_odds.py",
        "build_closing_lines.py",
        "compute_pick_clv.py",
        "audit_market_intelligence.py",
        "report_market.py",
    ]:
        path = _scripts_dir / script
        assert_true(f"{script} existe", path.exists())
        if path.exists():
            try:
                ast.parse(path.read_text(encoding="utf-8"))
                ok(f"{script} sintaxis OK")
            except SyntaxError as exc:
                fail(f"{script} sintaxis", str(exc))


# -- Group 31: partido.py -- Phase 12 enriquecimiento --------------------------

def test_partido_enrichment() -> None:
    section("31. partido.py -- Phase 12 market enrichment")
    partido_path = _src_root / "app" / "bot" / "handlers" / "partido.py"
    src = partido_path.read_text(encoding="utf-8")

    assert_in("market_intelligence importado", "get_fixture_market_summary", src)
    assert_in("market_summary variable", "market_summary", src)
    assert_in("_format_market_summary definido", "_format_market_summary", src)
    assert_in("guard MARKET_INTELLIGENCE_ENABLED", "market_intelligence_enabled", src)


# -- Group 32: conversation.py -- market intents routed ------------------------

def test_conversation_routing() -> None:
    section("32. conversation.py -- market intents routed")
    conv_path = _src_root / "app" / "bot" / "handlers" / "conversation.py"
    src = conv_path.read_text(encoding="utf-8")

    assert_in("fixture_market route", '"fixture_market"', src)
    assert_in("clv_summary route", '"clv_summary"', src)
    assert_in("mercado_handler imported in conv", "mercado_handler", src)
    assert_in("clv_handler imported in conv", "clv_handler", src)


# -- Runner --------------------------------------------------------------------

def main() -> None:
    print("\n" + "=" * 60)
    print("  TEST -- Market Intelligence Phase 12")
    print("=" * 60)

    groups = [
        test_schema_tables,
        test_sequences,
        test_upsert_odds_snapshot,
        test_get_odds_history,
        test_get_opening_odds,
        test_upsert_closing_line,
        test_get_closing_lines_for_fixture,
        test_upsert_pick_clv,
        test_get_clv_summary,
        test_upsert_market_signal,
        test_get_market_intelligence_summary,
        test_get_market_stats,
        test_service_implied,
        test_service_movement_label,
        test_service_clv_result,
        test_service_detect_signal_type,
        test_build_closing_lines,
        test_compute_pick_clv,
        test_detect_market_signals,
        test_get_fixture_market_summary,
        test_get_clv_report,
        test_parse_odds_response_item,
        test_ai_router_market_intents,
        test_handler_source,
        test_main_registration,
        test_config_settings,
        test_init_local_db_tables,
        test_scheduled_jobs_source,
        test_scheduler_service_source,
        test_cli_syntax,
        test_partido_enrichment,
        test_conversation_routing,
    ]

    for group_fn in groups:
        try:
            group_fn()
        except Exception as exc:
            fail(group_fn.__name__, f"EXCEPCION: {traceback.format_exc()}")

    print(f"\n{'=' * 60}")
    print(f"  Resultado: {_PASS} pasados, {_FAIL} fallidos")
    if _ERRORS:
        print(f"\n  Fallos:")
        for e in _ERRORS:
            print(f"    FAIL {e}")
    print(f"{'=' * 60}\n")

    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    main()
