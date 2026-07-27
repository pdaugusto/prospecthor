import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras
load_dotenv()
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
cur.execute(
    """
    SELECT id, name, source, lead_score, city
    FROM companies
    WHERE assigned_to = 9
    ORDER BY lead_score DESC NULLS LAST, id
    """
)
rows = cur.fetchall()
print(f"Blak total={len(rows)}")
for r in rows:
    print(f"  {r['lead_score']:>3}  {(r.get('source') or '?'):12}  {r['name'][:40]}  · {r.get('city')}")
cur.close()
conn.close()
