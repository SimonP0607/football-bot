# -*- coding: utf-8 -*-
import duckdb

con = duckdb.connect(r"./data/local/football_history.duckdb")

rows = con.execute("""
select league_name, season, count(*) as fixtures
from fixtures_history
where provider_league_id = 1
group by 1,2
order by season desc
""").fetchall()

print(rows)
con.close()
