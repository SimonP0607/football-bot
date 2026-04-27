# -*- coding: utf-8 -*-
from pathlib import Path
import duckdb
import csv
from collections import defaultdict

db = Path(r"./data/local/football_history.duckdb")
out_dir = Path(r"./data/local")
out_dir.mkdir(parents=True, exist_ok=True)

if not db.exists():
    raise SystemExit(f"No existe la base: {db}")

con = duckdb.connect(str(db))

# Detectar columnas reales de fixtures_history
cols = [r[1] for r in con.execute("PRAGMA table_info('fixtures_history')").fetchall()]
league_id_col = "provider_league_id" if "provider_league_id" in cols else "league_id"
league_name_col = "league_name"
season_col = "season"
fixture_id_col = "provider_fixture_id" if "provider_fixture_id" in cols else ("fixture_id" if "fixture_id" in cols else None)

query = f"""
select
    {league_id_col} as league_id,
    {league_name_col} as league_name,
    {season_col} as season,
    count(*) as fixtures_count
from fixtures_history
group by 1,2,3
order by league_name asc, season desc
"""

rows = con.execute(query).fetchall()

print("=== LIGAS HISTÓRICAS EN DUCKDB ===")
for league_id, league_name, season, fixtures_count in rows:
    print(f"{league_id} | {league_name} | {season} | fixtures={fixtures_count}")

# Resumen agrupado por liga
grouped = defaultdict(list)
for league_id, league_name, season, fixtures_count in rows:
    grouped[(league_id, league_name)].append((season, fixtures_count))

print("\n=== RESUMEN AGRUPADO POR LIGA ===")
for (league_id, league_name), vals in sorted(grouped.items(), key=lambda x: x[0][1].lower()):
    seasons_txt = ", ".join([f"{season} ({fixtures_count})" for season, fixtures_count in vals])
    print(f"{league_id} | {league_name} | temporadas: {seasons_txt}")

# Export detalle CSV
detail_csv = out_dir / "historic_leagues_detail.csv"
with detail_csv.open("w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["league_id", "league_name", "season", "fixtures_count"])
    w.writerows(rows)

# Export resumen CSV
summary_csv = out_dir / "historic_leagues_summary.csv"
with summary_csv.open("w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["league_id", "league_name", "seasons_loaded"])
    for (league_id, league_name), vals in sorted(grouped.items(), key=lambda x: x[0][1].lower()):
        seasons_txt = ", ".join([str(season) for season, _ in vals])
        w.writerow([league_id, league_name, seasons_txt])

print(f"\nCSV detalle: {detail_csv}")
print(f"CSV resumen: {summary_csv}")

# Chequeo simple de duplicados por fixture
if fixture_id_col:
    dup_query = f"""
    select
        {league_id_col} as league_id,
        {league_name_col} as league_name,
        {season_col} as season,
        count(*) as total_rows,
        count(distinct {fixture_id_col}) as unique_fixtures
    from fixtures_history
    group by 1,2,3
    having count(*) <> count(distinct {fixture_id_col})
    order by league_name, season desc
    """
    dup_rows = con.execute(dup_query).fetchall()

    print("\n=== CHEQUEO DE DUPLICADOS ===")
    if dup_rows:
        for r in dup_rows:
            print(f"DUPLICADOS -> league_id={r[0]} | league={r[1]} | season={r[2]} | total_rows={r[3]} | unique_fixtures={r[4]}")
    else:
        print("No se detectaron duplicados por fixture en fixtures_history.")

con.close()
