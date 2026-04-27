from pathlib import Path
import duckdb

db = Path(r"./data/local/football_history.duckdb")
wal = Path(r"./data/local/football_history.duckdb.wal")

print("=== tamaños ===")
for p in [db, wal]:
    if p.exists():
        print(f"{p.name}: {round(p.stat().st_size / 1024 / 1024, 2)} MB")
    else:
        print(f"{p.name}: no existe")

con = duckdb.connect(str(db))

print("\n=== conteos globales ===")
for table in ["fixtures_history", "standings_history", "team_stats_history"]:
    n = con.execute(f"select count(*) from {table}").fetchone()[0]
    print(f"{table}: {n}")

print("\n=== fixtures por liga/temporada ===")
rows = con.execute("""
select league_name, season, count(*) as fixtures
from fixtures_history
group by 1,2
order by league_name, season desc
""").fetchall()
for r in rows:
    print(r)

print("\n=== standings por liga/temporada ===")
rows = con.execute("""
select league_name, season, count(*) as standings_rows
from standings_history
group by 1,2
order by league_name, season desc
""").fetchall()
for r in rows:
    print(r)

print("\n=== team_stats por temporada ===")
rows = con.execute("""
select provider_league_id, season, count(*) as team_stats_rows
from team_stats_history
group by 1,2
order by provider_league_id, season desc
""").fetchall()
for r in rows:
    print(r)

con.close()
