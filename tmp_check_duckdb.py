import duckdb

con = duckdb.connect(r"./data/local/football_history.duckdb")

print("fixtures_history:")
print(con.execute("PRAGMA table_info('fixtures_history')").fetchall())

print("\nstandings_history:")
print(con.execute("PRAGMA table_info('standings_history')").fetchall())

print("\nteam_stats_history:")
print(con.execute("PRAGMA table_info('team_stats_history')").fetchall())

print("\nfixtures por temporada:")
print(con.execute("""
select league_name, season, count(*) as fixtures
from fixtures_history
where league_name = 'Premier League'
group by 1,2
order by season desc
""").fetchall())

print("\nstandings por temporada:")
print(con.execute("""
select league_name, season, count(*) as standings_rows
from standings_history
where league_name = 'Premier League'
group by 1,2
order by season desc
""").fetchall())

print("\nteam stats por temporada:")
print(con.execute("""
select season, count(*) as team_stats_rows
from team_stats_history
group by 1
order by season desc
""").fetchall())
