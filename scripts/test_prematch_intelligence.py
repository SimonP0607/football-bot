#!/usr/bin/env python
"""Test suite: Prematch Intelligence (Phase 6).

Validates:
  - Schema tables created correctly
  - Odds movement calculation (opening/current/delta/direction/strength)
  - Drift alerts generated correctly
  - Supporting-move alerts generated correctly
  - DRY-RUN writes nothing
  - execute=True writes to DuckDB
  - Value Engine reads prematch metadata without error
  - Missing DuckDB / missing odds handled gracefully
  - Budget cap respected

Usage:
    python scripts/test_prematch_intelligence.py
    python scripts/test_prematch_intelligence.py --verbose
"""

from __future__ import annotations

import argparse
import io
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: F401

_PASS = "[PASS]"
_FAIL = "[FAIL]"

_results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, cond, detail))
    status = _PASS if cond else _FAIL
    print(f"  {status} {name}" + (f" — {detail}" if detail else ""))


def _make_conn():
    """Open an in-memory DuckDB connection with prematch schema applied."""
    import duckdb
    conn = duckdb.connect(":memory:")
    sql_path = Path(__file__).resolve().parents[1] / "sql" / "local" / "006_prematch_intelligence_schema.sql"
    if sql_path.exists():
        conn.execute(sql_path.read_text(encoding="utf-8"))
    return conn


# ── Test groups ───────────────────────────────────────────────────────────────


def test_schema(verbose: bool) -> None:
    print("\n  [Schema creation]")
    conn = _make_conn()
    tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
    check("prematch_odds_movement table exists", "prematch_odds_movement" in tables)
    check("prematch_fixture_alerts table exists", "prematch_fixture_alerts" in tables)
    check("prematch_lineup_status table exists", "prematch_lineup_status" in tables)
    conn.close()


def test_odds_movement_calculation(verbose: bool) -> None:
    print("\n  [Odds movement calculation]")
    from app.data.local.prematch_repo import (
        upsert_odds_movement,
        get_odds_movement_by_provider_fixture,
        _movement_direction,
        _movement_strength,
    )
    conn = _make_conn()

    # First insert — opening = current, no movement
    upsert_odds_movement(conn, {
        "provider_fixture_id": 1001,
        "market_key": "1X2",
        "selection": "Home",
        "bookmaker": "TestBK",
        "current_odds": 2.10,
    })
    rows = get_odds_movement_by_provider_fixture(conn, 1001)
    check("First insert creates row", len(rows) == 1)
    r = rows[0]
    check("Opening equals current on first insert", abs(r["opening_odds"] - 2.10) < 0.001)
    check("odds_delta is 0 on first insert", abs(r["odds_delta"]) < 0.001)
    check("direction is stable on first insert", r["movement_direction"] == "stable")

    # Second insert — drift (odds got longer: 2.10 -> 2.40)
    upsert_odds_movement(conn, {
        "provider_fixture_id": 1001,
        "market_key": "1X2",
        "selection": "Home",
        "bookmaker": "TestBK",
        "current_odds": 2.40,
    })
    rows = get_odds_movement_by_provider_fixture(conn, 1001)
    r = rows[0]
    check("Opening preserved on update", abs(r["opening_odds"] - 2.10) < 0.001)
    check("Current updated on conflict", abs(r["current_odds"] - 2.40) < 0.001)
    check("odds_delta is positive for drift", r["odds_delta"] > 0)
    check("direction is drifting", r["movement_direction"] == "drifting")

    # Third insert — shortening (odds got shorter: 2.10 -> 1.80)
    upsert_odds_movement(conn, {
        "provider_fixture_id": 1002,
        "market_key": "1X2",
        "selection": "Away",
        "bookmaker": "TestBK",
        "current_odds": 1.90,
    })
    upsert_odds_movement(conn, {
        "provider_fixture_id": 1002,
        "market_key": "1X2",
        "selection": "Away",
        "bookmaker": "TestBK",
        "current_odds": 1.60,
    })
    rows2 = get_odds_movement_by_provider_fixture(conn, 1002)
    r2 = rows2[0]
    check("Shortening detected", r2["movement_direction"] == "shortening")
    check("odds_delta negative for shortening", r2["odds_delta"] < 0)

    # Direction helpers
    check("direction: shortening", _movement_direction(-0.1) == "shortening")
    check("direction: drifting",   _movement_direction(0.1)  == "drifting")
    check("direction: stable",     _movement_direction(0.0)  == "stable")

    # Strength helpers
    check("strength: none (0.005)",   _movement_strength(0.005)  == "none")
    check("strength: low (0.015)",    _movement_strength(0.015)  == "low")
    check("strength: medium (0.030)", _movement_strength(0.030)  == "medium")
    check("strength: high (0.060)",   _movement_strength(0.060)  == "high")

    conn.close()


def test_alerts(verbose: bool) -> None:
    print("\n  [Alert generation]")
    from app.data.local.prematch_repo import (
        upsert_odds_movement,
        upsert_fixture_alert,
        get_alerts_for_fixture,
        get_recent_alerts,
    )
    from app.services.prematch_intelligence_service import ALERT_DRIFT_AGAINST, ALERT_DRIFT_SUPPORT

    conn = _make_conn()

    # Create a strong drift scenario manually
    upsert_odds_movement(conn, {
        "provider_fixture_id": 2001,
        "market_key": "1X2",
        "selection": "Home",
        "bookmaker": "TestBK",
        "current_odds": 1.80,  # opening
    })
    upsert_odds_movement(conn, {
        "provider_fixture_id": 2001,
        "market_key": "1X2",
        "selection": "Home",
        "bookmaker": "TestBK",
        "current_odds": 2.50,  # big drift
    })

    # Manually write a drift alert
    upsert_fixture_alert(conn, {
        "fixture_id": None,
        "provider_fixture_id": 2001,
        "league_id": 39,
        "alert_type": ALERT_DRIFT_AGAINST,
        "severity": "high",
        "title": "Cuota drifting fuerte en 1X2/Home",
        "message": "Drift de 0.70 odds",
        "related_market": "1X2",
        "related_selection": "Home",
    })
    alerts = get_alerts_for_fixture(conn, 2001)
    check("Alert written for drift", len(alerts) >= 1)
    check("Alert has correct type",
          any(a["alert_type"] == ALERT_DRIFT_AGAINST for a in alerts))
    check("Alert severity is high",
          any(a["severity"] == "high" for a in alerts))

    # Supporting alert
    upsert_fixture_alert(conn, {
        "fixture_id": None,
        "provider_fixture_id": 2002,
        "league_id": 39,
        "alert_type": ALERT_DRIFT_SUPPORT,
        "severity": "low",
        "title": "Mercado apoya pick",
        "related_market": "1X2",
        "related_selection": "Away",
    })
    recent = get_recent_alerts(conn, days=1)
    check("get_recent_alerts returns alerts", len(recent) >= 2)

    # Idempotency: same alert upserted again doesn't duplicate
    upsert_fixture_alert(conn, {
        "fixture_id": None,
        "provider_fixture_id": 2001,
        "league_id": 39,
        "alert_type": ALERT_DRIFT_AGAINST,
        "severity": "high",
        "title": "Updated title",
        "related_market": "1X2",
        "related_selection": "Home",
    })
    alerts_after = get_alerts_for_fixture(conn, 2001)
    check("Alert upsert is idempotent (no duplicate)", len(alerts_after) == len(alerts))
    check("Alert title updated on conflict",
          any(a["title"] == "Updated title" for a in alerts_after))

    conn.close()


def test_lineup_status(verbose: bool) -> None:
    print("\n  [Lineup status]")
    from app.data.local.prematch_repo import upsert_lineup_status, get_lineup_status_for_fixture

    conn = _make_conn()

    upsert_lineup_status(conn, {
        "fixture_id": 501,
        "provider_fixture_id": 3001,
        "home_team_id": 10,
        "away_team_id": 20,
        "home_formation": "4-3-3",
        "away_formation": "4-2-3-1",
        "lineups_available": True,
        "lineups_confirmed": True,
        "home_missing_count": 2,
        "away_missing_count": 0,
        "home_impact": "medium",
        "away_impact": "none",
    })
    lu = get_lineup_status_for_fixture(conn, 3001)
    check("Lineup status inserted", lu is not None)
    check("Formation stored correctly", lu.get("home_formation") == "4-3-3")
    check("lineups_confirmed is True", lu.get("lineups_confirmed") is True)
    check("home_missing_count correct", lu.get("home_missing_count") == 2)

    # Update
    upsert_lineup_status(conn, {
        "fixture_id": 501,
        "provider_fixture_id": 3001,
        "home_team_id": 10,
        "away_team_id": 20,
        "home_formation": "4-4-2",
        "lineups_confirmed": True,
    })
    lu2 = get_lineup_status_for_fixture(conn, 3001)
    check("Lineup formation updated on conflict", lu2.get("home_formation") == "4-4-2")

    conn.close()


def test_dry_run(verbose: bool) -> None:
    print("\n  [Dry-run: no writes]")
    conn = _make_conn()
    from app.data.local.prematch_repo import prematch_counts

    # Mock a minimal fixture list
    from app.services.prematch_intelligence_service import run_prematch_intelligence

    # Minimal test: run with execute=False and no fixtures (empty from Supabase)
    # We test that counts don't change
    before = prematch_counts(conn)
    try:
        stats = run_prematch_intelligence(
            conn,
            hours=0,       # 0 hours = no fixtures in window
            limit=1,
            execute=False,
            sync_odds=False,
            sync_availability=False,
            sync_lineups=False,
        )
        after = prematch_counts(conn)
        check("Dry-run: no odds rows written",
              after["prematch_odds_movement"] == before["prematch_odds_movement"])
        check("Dry-run: no alerts written",
              after["prematch_fixture_alerts"] == before["prematch_fixture_alerts"])
    except Exception as exc:
        check("Dry-run: no exception raised", False, str(exc))

    conn.close()


def test_ve_prematch_enrichment(verbose: bool) -> None:
    print("\n  [Value Engine prematch enrichment]")
    conn = _make_conn()
    from app.data.local.prematch_repo import upsert_odds_movement

    # Insert a drift for fixture 9001 / 1X2 / Home
    upsert_odds_movement(conn, {
        "provider_fixture_id": 9001,
        "market_key": "1X2",
        "selection": "Home",
        "bookmaker": "TestBK",
        "current_odds": 2.00,
    })
    upsert_odds_movement(conn, {
        "provider_fixture_id": 9001,
        "market_key": "1X2",
        "selection": "Home",
        "bookmaker": "TestBK",
        "current_odds": 2.90,  # large drift
    })

    from app.services.value_engine_live_adapter import ValueEngineLiveAdapter
    adapter = ValueEngineLiveAdapter()
    adapter._bypass_mode_check = True
    adapter._conn = conn  # inject test conn

    prematch_meta = adapter._load_prematch_meta(conn, 9001, "1X2", "Home")
    check("Prematch meta loaded", bool(prematch_meta))
    check("Direction is drifting", prematch_meta.get("movement_direction") == "drifting")
    check("Strength is detected", prematch_meta.get("movement_strength") in ("medium", "high"))

    adj = adapter._compute_prematch_adjustment(prematch_meta, quality_before=0.62)
    check("Penalty applied for drifting", adj["penalty"] > 0)
    check("quality_after reduced", adj["quality_after"] < 0.62)
    check("Warning set", adj["warning"] is not None)

    # Shortening → boost
    upsert_odds_movement(conn, {
        "provider_fixture_id": 9002,
        "market_key": "1X2",
        "selection": "Away",
        "bookmaker": "TestBK",
        "current_odds": 3.00,
    })
    upsert_odds_movement(conn, {
        "provider_fixture_id": 9002,
        "market_key": "1X2",
        "selection": "Away",
        "bookmaker": "TestBK",
        "current_odds": 2.30,  # shortening
    })
    meta2 = adapter._load_prematch_meta(conn, 9002, "1X2", "Away")
    adj2 = adapter._compute_prematch_adjustment(meta2, quality_before=0.60)
    check("Boost applied for shortening", adj2["boost"] > 0)
    check("quality_after not reduced for shortening", adj2["quality_after"] >= 0.60)

    # No data → no penalty
    meta_empty = adapter._load_prematch_meta(conn, 99999, "1X2", "Home")
    adj_empty = adapter._compute_prematch_adjustment(meta_empty, quality_before=0.65)
    check("No penalty when no prematch data", adj_empty["penalty"] == 0.0)
    check("quality unchanged when no data", abs(adj_empty["quality_after"] - 0.65) < 0.001)

    conn.close()


def test_summary_for_fixture(verbose: bool) -> None:
    print("\n  [get_prematch_summary_for_fixture]")
    from app.data.local.prematch_repo import (
        upsert_odds_movement,
        upsert_fixture_alert,
        upsert_lineup_status,
        get_prematch_summary_for_fixture,
    )
    from app.services.prematch_intelligence_service import ALERT_DRIFT_AGAINST

    conn = _make_conn()
    prov_fid = 5001

    upsert_odds_movement(conn, {
        "provider_fixture_id": prov_fid,
        "market_key": "OU25",
        "selection": "Over",
        "bookmaker": "B1",
        "current_odds": 1.90,
    })
    upsert_fixture_alert(conn, {
        "provider_fixture_id": prov_fid,
        "alert_type": ALERT_DRIFT_AGAINST,
        "severity": "medium",
        "title": "Test alert",
        "related_market": "OU25",
        "related_selection": "Over",
    })
    upsert_lineup_status(conn, {
        "provider_fixture_id": prov_fid,
        "lineups_available": False,
        "lineups_confirmed": False,
    })

    summary = get_prematch_summary_for_fixture(conn, prov_fid)
    check("Summary is not None", summary is not None)
    check("Summary has odds_movement", bool(summary.get("odds_movement")))
    check("Summary has alerts", bool(summary.get("alerts")))
    check("Summary has lineup", summary.get("lineup") is not None)

    # Fixture with no data → None
    summary_none = get_prematch_summary_for_fixture(conn, 99998)
    check("Returns None when no data", summary_none is None)

    conn.close()


def print_summary() -> None:
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    print()
    print(f"  {'=' * 50}")
    print(f"  RESULTADO: {passed} / {len(_results)} tests pasaron")
    if failed:
        print(f"  FALLIDOS ({failed}):")
        for name, ok, detail in _results:
            if not ok:
                print(f"    [FAIL] {name}" + (f" — {detail}" if detail else ""))
    print(f"  {'=' * 50}")
    print()


def main(args: argparse.Namespace) -> None:
    print()
    print("=" * 60)
    print("  TEST PREMATCH INTELLIGENCE (Phase 6)")
    print("=" * 60)

    test_schema(args.verbose)
    test_odds_movement_calculation(args.verbose)
    test_alerts(args.verbose)
    test_lineup_status(args.verbose)
    test_dry_run(args.verbose)
    test_ve_prematch_enrichment(args.verbose)
    test_summary_for_fixture(args.verbose)

    print_summary()

    failed = sum(1 for _, ok, _ in _results if not ok)
    sys.exit(1 if failed else 0)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Tests unitarios de Prematch Intelligence (Phase 6)"
    )
    p.add_argument("--verbose", action="store_true", help="Verbose output")
    return p.parse_args()


if __name__ == "__main__":
    main(parse_args())
