"""Corrige conta admin (amigo): client, label editável, active livre."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from src.users import ensure_schema, list_users, update_user, _connect
import psycopg2.extras

ensure_schema()
conn = _connect()
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# força admin = client e tira label Patrão se colou errado
cur.execute(
    """
    UPDATE app_users
    SET role = 'client',
        active = 1,
        monthly_quota = CASE WHEN monthly_quota >= 9999 THEN 100 ELSE monthly_quota END,
        label = CASE
            WHEN label IS NULL OR TRIM(label) = '' OR label IN ('Patrão', 'Administrador')
            THEN 'Amigo'
            ELSE label
        END
    WHERE lower(username) = 'admin'
    RETURNING id, username, role, active, label, monthly_quota;
    """
)
print("admin fixed:", dict(cur.fetchone() or {}))

# patrao continua admin
cur.execute(
    """
    UPDATE app_users
    SET role = 'admin', active = 1, monthly_quota = 9999,
        label = CASE WHEN label IS NULL OR TRIM(label) = '' THEN 'Patrão' ELSE label END
    WHERE lower(username) = 'patrao'
    RETURNING id, username, role, label;
    """
)
print("patrao:", dict(cur.fetchone() or {}))

conn.commit()
cur.close()
conn.close()

# testa desativar e reativar + renomear
users = list_users()
amigo = next(u for u in users if u["username"] == "admin")
print("before", amigo["label"], amigo["active"])
update_user(amigo["id"], label="Amigo Cliente", active=0)
u = next(x for x in list_users() if x["id"] == amigo["id"])
print("after off+rename", u["label"], u["active"])
assert u["label"] == "Amigo Cliente"
assert int(u["active"]) == 0
update_user(amigo["id"], active=1, label="Amigo")
u = next(x for x in list_users() if x["id"] == amigo["id"])
print("restored", u["label"], u["active"])
assert int(u["active"]) == 1
print("OK")
