"""One-off: inspect leads in Supabase."""
from dotenv import load_dotenv
import os
import psycopg2
import psycopg2.extras

load_dotenv()
conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

cur.execute("SELECT COUNT(*) AS n FROM companies")
print("total companies:", cur.fetchone()["n"])

cur.execute(
    "SELECT lead_class, COUNT(*) AS n FROM companies GROUP BY lead_class ORDER BY n DESC"
)
print("by lead_class:", [dict(r) for r in cur.fetchall()])

cur.execute(
    "SELECT website_status, COUNT(*) AS n FROM companies GROUP BY website_status ORDER BY n DESC"
)
print("by website_status:", [dict(r) for r in cur.fetchall()])

cur.execute(
    """
    SELECT COUNT(*) AS n FROM companies
    WHERE website IS NULL OR TRIM(COALESCE(website, '')) = ''
       OR website_status IN ('sem_site', 'so_social')
    """
)
print("sem site (campo):", cur.fetchone()["n"])

cur.execute("SELECT COUNT(*) AS n FROM companies WHERE lead_class = 'raio'")
print("lead_class raio:", cur.fetchone()["n"])

cur.execute(
    """
    SELECT id, name, city, state, niche, website, website_status,
           lead_class, lead_score, phone, scored_at
    FROM companies
    WHERE website IS NULL OR TRIM(COALESCE(website, '')) = ''
       OR website_status IN ('sem_site', 'so_social')
       OR lead_class = 'raio'
    ORDER BY id DESC
    LIMIT 40
    """
)
print("--- lista sem site / raio ---")
for r in cur.fetchall():
    print(dict(r))

cur.execute(
    """
    SELECT city, niche, COUNT(*) AS n
    FROM companies
    GROUP BY city, niche
    ORDER BY n DESC
    LIMIT 25
    """
)
print("by city/niche:", [dict(r) for r in cur.fetchall()])

cur.execute(
    """
    SELECT COUNT(*) AS n FROM companies
    WHERE website IS NOT NULL AND TRIM(website) <> ''
      AND website_status IS DISTINCT FROM 'sem_site'
      AND website_status IS DISTINCT FROM 'so_social'
    """
)
print("com site real (aprox):", cur.fetchone()["n"])

cur.close()
conn.close()
