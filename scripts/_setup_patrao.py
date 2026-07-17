"""Define Patrão como admin principal e admin como cliente."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from src.users import ensure_schema, _hash_password, list_users, _connect
import psycopg2.extras

ensure_schema()
conn = _connect()
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# 1) admin vira client (conta do amigo)
cur.execute(
    """
    UPDATE app_users
    SET role = 'client',
        monthly_quota = CASE WHEN monthly_quota >= 9999 THEN 100 ELSE monthly_quota END,
        label = CASE
            WHEN label IS NULL OR label = '' OR label = 'Administrador' THEN 'Amigo'
            ELSE label
        END,
        active = 1
    WHERE lower(username) = 'admin'
    RETURNING id, username, role, monthly_quota, label;
    """
)
print("admin demoted:", dict(cur.fetchone() or {}))

# 2) Patrão = admin principal (login: patrao)
ph = _hash_password("Ronaldete1")
cur.execute(
    "SELECT id FROM app_users WHERE lower(username) IN ('patrao', 'patrão', 'patrao') LIMIT 1;"
)
row = cur.fetchone()
if row:
    cur.execute(
        """
        UPDATE app_users
        SET username = 'patrao',
            password_hash = %s,
            role = 'admin',
            monthly_quota = 9999,
            active = 1,
            label = 'Patrão'
        WHERE id = %s
        RETURNING id, username, role, label;
        """,
        (ph, row["id"]),
    )
    print("patrao updated:", dict(cur.fetchone()))
else:
    cur.execute(
        """
        INSERT INTO app_users (username, password_hash, role, monthly_quota, active, label)
        VALUES ('patrao', %s, 'admin', 9999, 1, 'Patrão')
        RETURNING id, username, role, label;
        """,
        (ph,),
    )
    print("patrao created:", dict(cur.fetchone()))

# 3) nenhum outro admin acidental
cur.execute(
    """
    UPDATE app_users
    SET role = 'client'
    WHERE role = 'admin' AND lower(username) <> 'patrao'
    RETURNING username;
    """
)
print("other admins demoted:", [dict(r) for r in cur.fetchall()])

conn.commit()
cur.close()
conn.close()

print("ALL:")
for u in list_users():
    print(
        f"  {u['username']:15} role={u['role']:6} quota={u['monthly_quota']} label={u['label']}"
    )
