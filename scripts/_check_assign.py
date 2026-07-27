"""Inspect users and assigned_to for isolation debug."""
from dotenv import load_dotenv
import os
import psycopg2
import psycopg2.extras

load_dotenv()
conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

cur.execute(
    "SELECT id, username, role, active, label, monthly_quota FROM app_users ORDER BY id"
)
print("USERS:")
for r in cur.fetchall():
    print(dict(r))

cur.execute(
    """
    SELECT assigned_to, COUNT(*) AS n
    FROM companies
    WHERE website_status IN ('sem_site', 'so_social')
       OR website IS NULL OR TRIM(COALESCE(website, '')) = ''
       OR lead_class = 'raio'
    GROUP BY assigned_to
    ORDER BY n DESC
    """
)
print("RAIO BY assigned_to:")
for r in cur.fetchall():
    print(dict(r))

# sample of assigned leads
cur.execute(
    """
    SELECT id, name, assigned_to
    FROM companies
    WHERE assigned_to IS NOT NULL
    ORDER BY id DESC
    LIMIT 10
    """
)
print("SAMPLE assigned:")
for r in cur.fetchall():
    print(dict(r))

cur.close()
conn.close()
