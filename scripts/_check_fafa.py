"""Check Fafa user status, quota, and free pool."""
from dotenv import load_dotenv
import os
from datetime import datetime
import psycopg2
import psycopg2.extras

load_dotenv()
conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

cur.execute(
    """
    SELECT id, username, role, active, label, monthly_quota
    FROM app_users
    ORDER BY id
    """
)
print("=== TODOS OS USUARIOS ===")
for u in cur.fetchall():
    print(dict(u))

cur.execute(
    """
    SELECT id, username, role, active, label, monthly_quota
    FROM app_users
    WHERE lower(username) LIKE %s OR lower(COALESCE(label,'')) LIKE %s
    ORDER BY id
    """,
    ("%fafa%", "%fafa%"),
)
users = cur.fetchall()
print("\n=== FAFA ===")
if not users:
    print("Nao encontrado")
else:
    for u in users:
        print(dict(u))
        uid = u["id"]
        month = datetime.now().strftime("%Y-%m")
        cur.execute(
            """
            SELECT COUNT(*) AS n FROM companies
            WHERE assigned_to = %s
              AND assigned_at IS NOT NULL
              AND assigned_at LIKE %s
            """,
            (uid, month + "%"),
        )
        used = cur.fetchone()["n"]
        cur.execute(
            "SELECT COUNT(*) AS n FROM companies WHERE assigned_to = %s",
            (uid,),
        )
        total = cur.fetchone()["n"]
        quota = int(u.get("monthly_quota") or 0)
        active = u.get("active")
        print(f"  active={active}  used_mes={used}/{quota}  total_na_conta={total}")
        if active in (1, True, "1"):
            print("  STATUS: ATIVO = SIM (pode receber do bot / Distribuir sobra)")
        else:
            print("  STATUS: ATIVO = NAO (bot NAO manda leads pra ele)")

cur.execute(
    """
    SELECT COUNT(*) AS n FROM companies
    WHERE assigned_to IS NULL
      AND (
        lead_class = 'raio'
        OR website_status IN ('sem_site', 'so_social')
        OR website IS NULL
        OR TRIM(COALESCE(website, '')) = ''
      )
    """
)
print("\n=== POOL ===")
print("sobras_livres:", cur.fetchone()["n"])

cur.close()
conn.close()
