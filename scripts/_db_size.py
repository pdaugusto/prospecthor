from dotenv import load_dotenv
import os
import psycopg2

load_dotenv()
url = os.getenv("DATABASE_URL", "")
print("provider:", "supabase" if "supabase" in url else "other/postgres")
if "@" in url:
    print("host:", url.split("@")[-1][:90])

conn = psycopg2.connect(url)
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM companies")
print("companies_rows:", cur.fetchone()[0])
cur.execute("SELECT pg_size_pretty(pg_database_size(current_database())), current_database()")
print("db_size / name:", cur.fetchone())
cur.execute("SELECT pg_size_pretty(pg_total_relation_size('companies'))")
print("companies_table_size:", cur.fetchone()[0])
cur.close()
conn.close()
