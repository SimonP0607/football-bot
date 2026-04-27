from pathlib import Path
import duckdb

db = Path("./data/local/football_history.duckdb")
print("db_exists =", db.exists(), "| path =", db)

con = duckdb.connect(str(db))

tables = [
    "fixtures_history",
    "standings_history",
    "team_stats_history",
    "competition_context",
    "team_elo_history",
    "training_samples",
    "calibration_registry",
    "market_quality_summary",
    "shadow_value_picks",
]

print("\n=== row counts ===")
for t in tables:
    try:
        n = con.execute(f"select count(*) from {t}").fetchone()[0]
        print(f"{t}: {n}")
    except Exception as e:
        print(f"{t}: ERROR -> {e}")

print("\n=== latest calibrators ===")
try:
    rows = con.execute("""
        select trained_at, market_key, entity_type, competition_type, provider_league_id, n_train
        from calibration_registry
        order by trained_at desc
        limit 10
    """).fetchall()
    for r in rows:
        print(r)
except Exception as e:
    print("calibration_registry check error:", e)

print("\n=== latest shadow picks ===")
try:
    rows = con.execute("""
        select run_date, fixture_id, provider_league_id, market_key, selection,
               p_raw, p_cal, p_mkt, p_adj, offered_odds, quality_score, decision_status
        from shadow_value_picks
        order by run_date desc, quality_score desc
        limit 20
    """).fetchall()
    for r in rows:
        print(r)
except Exception as e:
    print("shadow_value_picks check error:", e)

print("\n=== market connection summary ===")
try:
    row = con.execute("""
        select
            count(*) as total_rows,
            count(p_raw) as with_p_raw,
            count(p_cal) as with_p_cal,
            count(p_mkt) as with_p_mkt,
            count(p_adj) as with_p_adj,
            count(offered_odds) as with_offered_odds
        from shadow_value_picks
    """).fetchone()
    print(row)
except Exception as e:
    print("market connection summary error:", e)

con.close()
