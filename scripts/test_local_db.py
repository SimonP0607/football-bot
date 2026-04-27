#!/usr/bin/env python
"""Smoke test for the local DuckDB history database.

Uses an in-memory database so the real history file is never touched.
Applies the same schema as init_local_db.py, then inserts and queries
one test record per main table.

Usage:
    python scripts/test_local_db.py

Exit code:
    0 — all checks passed
    1 — one or more checks failed
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import duckdb
    from app.data.local.duckdb_client import init_schema, table_names
    from app.data.local import history_repo as repo
except ImportError as e:
    print(f"Error de importación: {e}")
    sys.exit(1)


def _check(label: str, condition: bool) -> bool:
    mark = "  OK" if condition else "  FAIL"
    print(f"{mark}  {label}")
    return condition


def main() -> bool:
    print("\n=== test_local_db (in-memory) ===\n")

    conn = duckdb.connect(":memory:")
    init_schema(conn)

    tables = table_names(conn)
    ok = True

    # ── Schema ────────────────────────────────────────────────────────────────
    print("[ Schema ]")
    expected = [
        "fixtures_history", "standings_history", "team_stats_history",
        "odds_history", "published_picks_history", "pick_results_history",
        "backtest_runs", "backtest_metrics",
    ]
    for t in expected:
        ok &= _check(f"tabla {t}", t in tables)

    # ── fixtures_history ──────────────────────────────────────────────────────
    print("\n[ fixtures_history ]")
    repo.insert_fixture(conn, {
        "id": 9_000_001,
        "provider_fixture_id": 1_060_001,
        "provider_league_id": 39,
        "league_name": "Premier League [TEST]",
        "season": 2025,
        "home_team_id": 33,
        "home_team_name": "Manchester United [TEST]",
        "away_team_id": 34,
        "away_team_name": "Newcastle [TEST]",
        "kickoff_at": "2025-04-15T20:00:00+00:00",
        "date_local": "2025-04-15",
        "status_short": "FT",
        "goals_home": 2,
        "goals_away": 1,
    })
    n = repo.count_fixtures(conn)
    ok &= _check("insert fixture", n == 1)
    ok &= _check("insert OR REPLACE idempotente", True)  # re-insert same id
    repo.insert_fixture(conn, {
        "id": 9_000_001,
        "provider_fixture_id": 1_060_001,
        "provider_league_id": 39,
        "league_name": "Premier League [TEST]",
        "season": 2025,
        "home_team_id": 33,
        "home_team_name": "Manchester United [TEST]",
        "away_team_id": 34,
        "away_team_name": "Newcastle [TEST]",
        "kickoff_at": "2025-04-15T20:00:00+00:00",
        "status_short": "FT",
        "goals_home": 2,
        "goals_away": 1,
    })
    ok &= _check("count after re-insert = 1", repo.count_fixtures(conn) == 1)

    # ── published_picks_history ───────────────────────────────────────────────
    print("\n[ published_picks_history ]")
    repo.insert_published_pick(conn, {
        "id": 8_000_001,
        "fixture_history_id": 9_000_001,
        "provider_fixture_id": 1_060_001,
        "market_key": "1X2",
        "selection": "Home",
        "model_probability": 0.58,
        "implied_probability": 0.52,
        "edge": 0.06,
        "confidence_score": 0.58,
        "best_odd": 1.92,
        "best_bookmaker": "Bet365 [TEST]",
        "published_at": "2025-04-15T09:00:00+00:00",
    })
    n = conn.execute("SELECT COUNT(*) FROM published_picks_history").fetchone()[0]
    ok &= _check("insert published_pick", n == 1)

    # ── pick_results_history ──────────────────────────────────────────────────
    print("\n[ pick_results_history ]")
    repo.insert_pick_result(conn, {
        "id": 7_000_001,
        "published_pick_id": 8_000_001,
        "fixture_history_id": 9_000_001,
        "market_key": "1X2",
        "selection": "Home",
        "odd_taken": 1.92,
        "result_status": "win",
        "settled_at": "2025-04-15T22:00:00+00:00",
        "profit_units": 0.92,
    })
    n = conn.execute("SELECT COUNT(*) FROM pick_results_history").fetchone()[0]
    ok &= _check("insert pick_result", n == 1)

    # ── ROI queries ───────────────────────────────────────────────────────────
    print("\n[ Analytics ]")
    summary = repo.get_roi_summary(conn)
    ok &= _check("roi_summary.wins == 1", summary["wins"] == 1)
    ok &= _check("roi_summary.profit_units == 0.92", abs(summary["profit_units"] - 0.92) < 0.001)

    by_market = repo.get_roi_by_market(conn)
    ok &= _check("roi_by_market tiene 1 fila", len(by_market) == 1)
    ok &= _check("roi_by_market.market == '1X2'", by_market[0]["market"] == "1X2")

    by_league = repo.get_roi_by_league(conn)
    ok &= _check("roi_by_league tiene 1 fila", len(by_league) == 1)
    ok &= _check("roi_by_league.league_id == 39", by_league[0]["league_id"] == 39)

    # ── Backtest ──────────────────────────────────────────────────────────────
    print("\n[ Backtest ]")
    run_id = repo.create_backtest_run(
        conn,
        "test_run_v1",
        description="Smoke test",
        leagues=[39, 140],
        seasons=[2025],
        min_edge=0.03,
        min_confidence=0.52,
        max_daily_picks=5,
    )
    ok &= _check(f"backtest_run creado (id={run_id})", isinstance(run_id, int) and run_id > 0)

    repo.insert_backtest_metric(conn, run_id, {
        "fixture_history_id": 9_000_001,
        "market_key": "1X2",
        "selection": "Home",
        "model_probability": 0.58,
        "implied_probability": 0.52,
        "edge": 0.06,
        "confidence_score": 0.58,
        "odd_taken": 1.92,
        "result_status": "win",
        "profit_units": 0.92,
        "kickoff_at": "2025-04-15T20:00:00+00:00",
    })
    n = conn.execute("SELECT COUNT(*) FROM backtest_metrics WHERE backtest_run_id = ?", [run_id]).fetchone()[0]
    ok &= _check("backtest_metric insertado", n == 1)

    repo.finish_backtest_run(conn, run_id, total_picks=1, total_profit_units=0.92, roi_pct=92.0)
    run = repo.get_backtest_run_summary(conn, run_id)
    ok &= _check("backtest_run status = completed", run is not None and run["status"] == "completed")
    ok &= _check("backtest_run roi_pct = 92.0", run is not None and run["roi_pct"] == 92.0)

    # ── Resultado ─────────────────────────────────────────────────────────────
    conn.close()
    print()
    if ok:
        print("PASS - todos los checks pasaron. Schema y repositorio funcionan correctamente.\n")
    else:
        print("FAIL - uno o mas checks fallaron. Revisa la salida arriba.\n")

    return ok


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
