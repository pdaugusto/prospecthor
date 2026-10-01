"""Ponte Supabase (HTTPS/443) -> Postgres local.

Puxa app_users do Supabase via REST e replica no banco local,
para o cockpit exibir "Quem vai receber" sem precisar da porta 5432.

Uso: venv/bin/python sync_supabase.py [--only-users]
"""
import json
import os
import sys
import urllib.request
import urllib.parse
from pathlib import Path

from dotenv import load_dotenv

# Âncora no diretório do projeto (este arquivo), NÃO no cwd:
# o cockpit pode ser iniciado de qualquer pasta via comando `prospecthor`.
ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

BASE = (os.getenv("SUPABASE_URL") or "").rstrip("/")
KEY = os.getenv("SUPABASE_SERVICE_KEY") or ""


def _local_uri() -> str:
    """URI do espelho local, lida na hora (nunca vazia silenciosa)."""
    try:
        with open(ROOT / ".env.local_db", encoding="utf-8") as f:
            uri = f.read().strip().split("=", 1)[1].strip()
        if uri:
            return uri
    except OSError:
        pass
    raise RuntimeError(
        "Espelho local indisponível (.env.local_db não encontrado) — "
        "rode `prospecthor` para ligar o banco local antes do push."
    )


def rest(path: str, params: str = ""):
    url = f"{BASE}/rest/v1/{path}{params}"
    req = urllib.request.Request(
        url,
        headers={
            "apikey": KEY,
            "Authorization": f"Bearer {KEY}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.load(res)


def sync_users() -> int:
    import psycopg2

    rows = rest("app_users", "?select=*&order=id&limit=1000")
    conn = psycopg2.connect(_local_uri(), connect_timeout=5)
    conn.autocommit = True
    cur = conn.cursor()
    n = 0
    for r in rows:
        cur.execute(
            """
            INSERT INTO app_users
                (id, username, password_hash, role, monthly_quota, active,
                 cities, niches, label, created_at, email, display_name,
                 whatsapp, trovoedas_balance, terms_accepted_at,
                 stripe_customer_id, plan_slug, daily_quota, signup_ip)
            VALUES
                (%(id)s, %(username)s, %(password_hash)s, %(role)s, %(monthly_quota)s, %(active)s,
                 %(cities)s, %(niches)s, %(label)s, %(created_at)s, %(email)s, %(display_name)s,
                 %(whatsapp)s, %(trovoedas_balance)s, %(terms_accepted_at)s,
                 %(stripe_customer_id)s, %(plan_slug)s, %(daily_quota)s, %(signup_ip)s)
            ON CONFLICT (id) DO UPDATE SET
                username = EXCLUDED.username,
                password_hash = EXCLUDED.password_hash,
                role = EXCLUDED.role,
                monthly_quota = EXCLUDED.monthly_quota,
                active = EXCLUDED.active,
                cities = EXCLUDED.cities,
                niches = EXCLUDED.niches,
                label = EXCLUDED.label,
                email = EXCLUDED.email,
                display_name = EXCLUDED.display_name,
                whatsapp = EXCLUDED.whatsapp,
                trovoedas_balance = EXCLUDED.trovoedas_balance,
                plan_slug = EXCLUDED.plan_slug,
                daily_quota = EXCLUDED.daily_quota;
            """,
            {
                "id": r.get("id"),
                "username": r.get("username"),
                "password_hash": r.get("password_hash"),
                "role": r.get("role") or "client",
                "monthly_quota": r.get("monthly_quota") or 50,
                "active": r.get("active", 1),
                "cities": r.get("cities") if isinstance(r.get("cities"), str) else json.dumps(r.get("cities") or []),
                "niches": r.get("niches") if isinstance(r.get("niches"), str) else json.dumps(r.get("niches") or []),
                "label": r.get("label") or "",
                "created_at": r.get("created_at"),
                "email": r.get("email") or "",
                "display_name": r.get("display_name") or "",
                "whatsapp": r.get("whatsapp") or "",
                "trovoedas_balance": r.get("trovoedas_balance") or 0,
                "terms_accepted_at": r.get("terms_accepted_at"),
                "stripe_customer_id": r.get("stripe_customer_id"),
                "plan_slug": r.get("plan_slug"),
                "daily_quota": r.get("daily_quota"),
                "signup_ip": r.get("signup_ip") or "",
            },
        )
        n += 1
    cur.execute("SELECT setval('app_users_id_seq', COALESCE((SELECT MAX(id) FROM app_users), 1));")
    cur.close()
    conn.close()
    return n


def _post(path: str, payload, params: str = ""):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/rest/v1/{path}{params}",
        data=data,
        method="POST",
        headers={
            "apikey": KEY,
            "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.status


def push_companies(since_id: int = 0) -> int:
    """Sobe leads locais (com score) p/ o Supabase via upsert por place_id.

    since_id > 0: envia só o que chegou depois (pós-missão).
    Só envia com scored_at preenchido (site exige nota).
    """
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(_local_uri(), connect_timeout=5)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT * FROM companies WHERE id > %s AND scored_at IS NOT NULL ORDER BY id;",
        (int(since_id or 0),),
    )
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()

    sent = 0
    batch: list[dict] = []
    for r in rows:
        r.pop("id", None)  # nuvem mantém os ids dela (place_id é a chave)
        batch.append(r)
        if len(batch) >= 50:
            _post("companies", batch, "?on_conflict=place_id")
            sent += len(batch)
            batch = []
    if batch:
        _post("companies", batch, "?on_conflict=place_id")
        sent += len(batch)
    return sent


def main() -> None:
    if not BASE or not KEY:
        print("Faltam SUPABASE_URL / SUPABASE_SERVICE_KEY no .env")
        sys.exit(1)
    if "--push" in sys.argv:
        n = push_companies()
        print(f"PUSH OK: {n} leads (com score) -> Supabase")
        return
    n = sync_users()
    print(f"SYNC OK: {n} usuarios do Supabase -> banco local")


if __name__ == "__main__":
    main()
