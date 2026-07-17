"""
Garante só 2 contas:
- patrao = admin (vê todos os leads + Usuários)
- admin  = client (amigo) com os leads que já tinha / 67 raios
"""
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from src.users import (
    ensure_schema,
    _hash_password,
    list_users,
    _connect,
)
import psycopg2.extras

ensure_schema()
conn = _connect()
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# --- usuários ---
ph_patrao = _hash_password("Ronaldete1")

# patrao admin
cur.execute("SELECT id FROM app_users WHERE lower(username) = 'patrao' LIMIT 1;")
row = cur.fetchone()
if row:
    cur.execute(
        """
        UPDATE app_users SET role='admin', password_hash=%s, monthly_quota=9999,
               active=1, label='Patrão'
        WHERE id=%s RETURNING id, username, role;
        """,
        (ph_patrao, row["id"]),
    )
else:
    cur.execute(
        """
        INSERT INTO app_users (username, password_hash, role, monthly_quota, active, label)
        VALUES ('patrao', %s, 'admin', 9999, 1, 'Patrão')
        RETURNING id, username, role;
        """,
        (ph_patrao,),
    )
patrao = dict(cur.fetchone())
print("patrao:", patrao)

# admin = amigo client
cur.execute("SELECT id FROM app_users WHERE lower(username) = 'admin' LIMIT 1;")
row = cur.fetchone()
if row:
    cur.execute(
        """
        UPDATE app_users SET role='client', monthly_quota=200, active=1, label='Amigo'
        WHERE id=%s RETURNING id, username, role, monthly_quota;
        """,
        (row["id"],),
    )
else:
    # senha padrão se não existir — amigo já tem a dele se existir
    cur.execute(
        """
        INSERT INTO app_users (username, password_hash, role, monthly_quota, active, label)
        VALUES ('admin', %s, 'client', 200, 1, 'Amigo')
        RETURNING id, username, role, monthly_quota;
        """,
        (_hash_password("senha123"),),
    )
amigo = dict(cur.fetchone())
print("admin (amigo):", amigo)
amigo_id = int(amigo["id"])

# desativa qualquer outro (teste_amigo etc.)
cur.execute(
    """
    UPDATE app_users SET active=0, role='client'
    WHERE lower(username) NOT IN ('patrao', 'admin')
    RETURNING username;
    """
)
print("desativados:", [r["username"] for r in cur.fetchall()])

# --- leads: admin fica com os raios (sem site) já existentes ---
# Conta raios atuais
cur.execute(
    """
    SELECT COUNT(*) AS n FROM companies
    WHERE lead_class = 'raio'
       OR website_status IN ('sem_site', 'so_social')
       OR website IS NULL OR TRIM(COALESCE(website,'')) = '';
    """
)
total_raio = cur.fetchone()["n"]
print("total raios no banco:", total_raio)

# Já atribuídos ao amigo
cur.execute(
    "SELECT COUNT(*) AS n FROM companies WHERE assigned_to = %s;",
    (amigo_id,),
)
already = cur.fetchone()["n"]
print("ja assigned ao admin:", already)

# Atribui todos os Raios livres (sem dono) ao amigo — ele mantém/ganha os 67
now = datetime.now().isoformat()
cur.execute(
    """
    UPDATE companies
    SET assigned_to = %s, assigned_at = COALESCE(assigned_at, %s)
    WHERE assigned_to IS NULL
      AND (
        lead_class = 'raio'
        OR website_status IN ('sem_site', 'so_social')
        OR website IS NULL
        OR TRIM(COALESCE(website,'')) = ''
      )
    """,
    (amigo_id, now),
)
print("atribuidos agora (livres → admin):", cur.rowcount)

# Se algum lead estava com outro user (teste), move pro admin também? só raios
cur.execute(
    """
    UPDATE companies
    SET assigned_to = %s, assigned_at = COALESCE(assigned_at, %s)
    WHERE assigned_to IS NOT NULL
      AND assigned_to <> %s
      AND assigned_to <> %s
      AND (
        lead_class = 'raio'
        OR website_status IN ('sem_site', 'so_social')
        OR website IS NULL
        OR TRIM(COALESCE(website,'')) = ''
      )
    """,
    (amigo_id, now, amigo_id, int(patrao["id"])),
)
print("movidos de outros users → admin:", cur.rowcount)

cur.execute(
    "SELECT COUNT(*) AS n FROM companies WHERE assigned_to = %s;",
    (amigo_id,),
)
print("total final assigned admin (amigo):", cur.fetchone()["n"])

# Leads com assigned_to = patrao ficam só se quiser — por padrão admin (amigo) tem os raios
# Patrão vê TODOS via role admin (não precisa assigned_to)

conn.commit()
cur.close()
conn.close()

print("\nUSERS:")
for u in list_users():
    if not u.get("active") and u["username"] not in ("patrao", "admin"):
        print(f"  (off) {u['username']}")
        continue
    print(
        f"  {u['username']:12} role={u['role']:6} active={u['active']} "
        f"quota={u['monthly_quota']} month={u.get('assigned_this_month')}"
    )
