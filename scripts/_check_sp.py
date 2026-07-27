from dotenv import load_dotenv
import os
import psycopg2
import psycopg2.extras

load_dotenv()
conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
cur.execute(
    """
    SELECT name, city, niche, website, website_status, lead_class, lead_score
    FROM companies
    WHERE city ILIKE '%paulo%'
    ORDER BY COALESCE(lead_score, 0) DESC, id
    """
)
rows = cur.fetchall()
print(f"SP total: {len(rows)}")
sem = 0
for r in rows:
    ws = r["website_status"] or "-"
    w = (r["website"] or "")[:50]
    empty = not (r["website"] or "").strip() or ws in ("sem_site", "so_social")
    if empty:
        sem += 1
        mark = "SEM_SITE"
    else:
        mark = "TEM_SITE"
    print(
        f"{mark:8} class={r['lead_class'] or '-':6} score={r['lead_score']} "
        f"status={ws:10} | {r['niche']:12} | {r['name'][:55]}"
    )
print(f"--- SEM site em SP: {sem} / {len(rows)} ---")
cur.close()
conn.close()
