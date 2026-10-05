import psycopg2
from ingest import DB

print(f"connecting to {DB['dbname']} as {DB['user']} on {DB['host']}")
with psycopg2.connect(**DB) as c:
    cur = c.cursor()
    cur.execute("select gameweek, count(*) from team_match_full group by gameweek order by gameweek")
print(cur.fetchall())