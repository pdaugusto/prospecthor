from dotenv import load_dotenv
import os
import psycopg2
import psycopg2.extras

load_dotenv()
conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
cur.execute(
    """
    SELECT city, state, niche, COUNT(*) AS n,
           SUM(CASE WHEN lead_class = 'raio' OR website_status = 'sem_site'
                    OR website IS NULL OR TRIM(COALESCE(website,'')) = '' THEN 1 ELSE 0 END) AS sem_site
    FROM companies
    GROUP BY city, state, niche
    ORDER BY n DESC
    """
)
for r in cur.fetchall():
    print(dict(r))
cur.close()
conn.close()
