"""
users.py — Multi-usuário + cota + distribuição de leads Raio

- admin: vê tudo, gerencia usuários, define cotas
- client: só vê leads assigned_to = ele
- Ao classificar Raio, assign_raio_lead() escolhe quem tem vaga na cota do mês
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime
from typing import Any

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("users")

_DATABASE_URL = os.getenv("DATABASE_URL", "")
# Admin principal do painel (Patrão). Pode sobrescrever no .env / Vercel.
_ENV_ADMIN_USER = os.getenv("DASHBOARD_USER", "patrao")
_ENV_ADMIN_PASS = os.getenv("DASHBOARD_PASS", "Ronaldete1")


def _hash_password(password: str) -> str:
    return hashlib.sha256((password or "").encode("utf-8")).hexdigest()


def _connect():
    if not _DATABASE_URL:
        raise RuntimeError("DATABASE_URL não configurada")
    return psycopg2.connect(_DATABASE_URL)


def ensure_schema() -> None:
    """Cria tabela de usuários e colunas de atribuição nos leads."""
    if not _DATABASE_URL:
        return
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS app_users (
                id              SERIAL PRIMARY KEY,
                username        TEXT UNIQUE NOT NULL,
                password_hash   TEXT NOT NULL,
                role            TEXT NOT NULL DEFAULT 'client',
                monthly_quota   INTEGER NOT NULL DEFAULT 50,
                active          INTEGER NOT NULL DEFAULT 1,
                cities          TEXT DEFAULT '[]',
                niches          TEXT DEFAULT '[]',
                label           TEXT DEFAULT '',
                created_at      TEXT NOT NULL DEFAULT (NOW()::TEXT)
            );
            """
        )
        # colunas em companies
        for col, typ in (
            ("assigned_to", "INTEGER"),
            ("assigned_at", "TEXT"),
        ):
            cur.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'companies' AND column_name = %s
                """,
                (col,),
            )
            if not cur.fetchone():
                cur.execute(f"ALTER TABLE companies ADD COLUMN {col} {typ};")

        # seed / garante admin principal (patrao)
        principal = (_ENV_ADMIN_USER or "patrao").lower()
        cur.execute(
            "SELECT id FROM app_users WHERE lower(username) = %s LIMIT 1;",
            (principal,),
        )
        if not cur.fetchone():
            cur.execute(
                """
                INSERT INTO app_users (username, password_hash, role, monthly_quota, active, label)
                VALUES (%s, %s, 'admin', 9999, 1, 'Patrão')
                """,
                (principal, _hash_password(_ENV_ADMIN_PASS)),
            )
            logger.warning("[Users] Admin principal criado: %s", principal)
        else:
            cur.execute(
                """
                UPDATE app_users
                SET role = 'admin', active = 1, monthly_quota = 9999,
                    label = 'Patrão'
                WHERE lower(username) = %s;
                """,
                (principal,),
            )
        # Ninguém mais pode ser admin (ex.: conta "admin" do amigo)
        cur.execute(
            """
            UPDATE app_users
            SET role = 'client',
                monthly_quota = CASE WHEN monthly_quota >= 9999 THEN 100 ELSE monthly_quota END,
                label = CASE
                    WHEN lower(username) = 'admin' AND (label IS NULL OR label IN ('', 'Administrador', 'Patrão'))
                    THEN 'Amigo' ELSE COALESCE(label, username)
                END
            WHERE role = 'admin' AND lower(username) <> %s;
            """,
            (principal,),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    """Valida login. Fallback: DASHBOARD_USER/PASS do .env como admin (Patrão)."""
    username = (username or "").strip()
    password = password or ""
    if not username:
        return None

    uname_l = username.lower()
    env_admin_l = (_ENV_ADMIN_USER or "patrao").lower()

    # Fallback env = Patrão (mesmo sem tabela / DB down)
    if uname_l == env_admin_l and password == _ENV_ADMIN_PASS:
        try:
            ensure_schema()
            u = get_user_by_username(env_admin_l)
            if u:
                u["role"] = "admin"
                return u
        except Exception:
            pass
        return {
            "id": 0,
            "username": env_admin_l,
            "role": "admin",
            "monthly_quota": 9999,
            "active": 1,
            "label": "Patrão",
            "cities": [],
            "niches": [],
        }

    try:
        ensure_schema()
        conn = _connect()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT id, username, role, monthly_quota, active, cities, niches, label
            FROM app_users
            WHERE lower(username) = lower(%s) AND password_hash = %s
            LIMIT 1;
            """,
            (username, _hash_password(password)),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return None
        user = dict(row)
        if not user.get("active"):
            return None
        user["cities"] = _parse_json_list(user.get("cities"))
        user["niches"] = _parse_json_list(user.get("niches"))
        return user
    except Exception as exc:
        logger.warning("[Users] auth falhou: %s", exc)
        return None


def _parse_json_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return [str(x) for x in data] if isinstance(data, list) else []
    except Exception:
        return []


def get_user_by_username(username: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT id, username, role, monthly_quota, active, cities, niches, label
            FROM app_users WHERE username = %s LIMIT 1;
            """,
            (username,),
        )
        row = cur.fetchone()
        cur.close()
        if not row:
            return None
        u = dict(row)
        u["cities"] = _parse_json_list(u.get("cities"))
        u["niches"] = _parse_json_list(u.get("niches"))
        return u
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    if not user_id:
        return None
    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT id, username, role, monthly_quota, active, cities, niches, label
            FROM app_users WHERE id = %s LIMIT 1;
            """,
            (user_id,),
        )
        row = cur.fetchone()
        cur.close()
        if not row:
            return None
        u = dict(row)
        u["cities"] = _parse_json_list(u.get("cities"))
        u["niches"] = _parse_json_list(u.get("niches"))
        return u
    finally:
        conn.close()


def list_users() -> list[dict[str, Any]]:
    ensure_schema()
    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT id, username, role, monthly_quota, active, cities, niches, label, created_at
            FROM app_users
            ORDER BY role DESC, username;
            """
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        # contagem do mês
        for u in rows:
            u["cities"] = _parse_json_list(u.get("cities"))
            u["niches"] = _parse_json_list(u.get("niches"))
            u["assigned_this_month"] = count_assigned_this_month(int(u["id"]))
        return rows
    finally:
        conn.close()


def count_assigned_this_month(user_id: int) -> int:
    if not user_id:
        return 0
    month_prefix = datetime.now().strftime("%Y-%m")
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) FROM companies
            WHERE assigned_to = %s
              AND assigned_at IS NOT NULL
              AND assigned_at LIKE %s;
            """,
            (user_id, f"{month_prefix}%"),
        )
        n = cur.fetchone()[0]
        cur.close()
        return int(n or 0)
    except Exception:
        return 0
    finally:
        conn.close()


def create_user(
    username: str,
    password: str,
    monthly_quota: int = 50,
    role: str = "client",
    cities: list[str] | None = None,
    niches: list[str] | None = None,
    label: str = "",
) -> dict[str, Any]:
    ensure_schema()
    username = (username or "").strip().lower()
    if not username or not password:
        raise ValueError("username e password obrigatórios")
    if role not in ("admin", "client"):
        role = "client"
    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            INSERT INTO app_users
                (username, password_hash, role, monthly_quota, active, cities, niches, label)
            VALUES (%s, %s, %s, %s, 1, %s, %s, %s)
            RETURNING id, username, role, monthly_quota, active, cities, niches, label;
            """,
            (
                username,
                _hash_password(password),
                role,
                int(monthly_quota or 50),
                json.dumps(cities or [], ensure_ascii=False),
                json.dumps(niches or [], ensure_ascii=False),
                label or username,
            ),
        )
        row = dict(cur.fetchone())
        conn.commit()
        cur.close()
        row["cities"] = cities or []
        row["niches"] = niches or []
        row["assigned_this_month"] = 0
        logger.warning("[Users] Criado: %s quota=%s", username, monthly_quota)
        return row
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_user(user_id: int, **fields: Any) -> dict[str, Any] | None:
    ensure_schema()
    allowed = {
        "monthly_quota",
        "active",
        "role",
        "label",
        "cities",
        "niches",
        "password",
    }
    sets = []
    params: list[Any] = []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "password":
            sets.append("password_hash = %s")
            params.append(_hash_password(str(v)))
        elif k in ("cities", "niches"):
            sets.append(f"{k} = %s")
            params.append(json.dumps(v if isinstance(v, list) else [], ensure_ascii=False))
        elif k == "active":
            sets.append("active = %s")
            params.append(1 if v in (True, 1, "1", "true") else 0)
        elif k == "monthly_quota":
            sets.append("monthly_quota = %s")
            params.append(int(v))
        else:
            sets.append(f"{k} = %s")
            params.append(v)
    if not sets:
        return get_user_by_id(user_id)
    params.append(user_id)
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE app_users SET {', '.join(sets)} WHERE id = %s;",
            params,
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return get_user_by_id(user_id)


def _user_accepts_lead(user: dict[str, Any], company: dict[str, Any]) -> bool:
    """Filtro opcional de cidade/nicho do usuário."""
    cities = user.get("cities") or []
    niches = user.get("niches") or []
    if cities:
        c = (company.get("city") or "").strip().lower()
        if not any(c == x.strip().lower() or x.strip().lower() in c for x in cities):
            return False
    if niches:
        n = (company.get("niche") or "").strip().lower()
        if n not in [x.strip().lower() for x in niches]:
            return False
    return True


def assign_raio_lead(company_id: int) -> int | None:
    """
    Atribui lead Raio a um cliente com vaga na cota do mês.
    Round-robin justo: quem tem menos leads no mês (e ainda tem quota).
    Retorna user_id ou None se ninguém puder receber.
    """
    if not company_id or not _DATABASE_URL:
        return None
    try:
        ensure_schema()
        conn = _connect()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM companies WHERE id = %s LIMIT 1;", (company_id,))
        company = cur.fetchone()
        if not company:
            cur.close()
            conn.close()
            return None
        company = dict(company)

        # já atribuído
        if company.get("assigned_to"):
            cur.close()
            conn.close()
            return int(company["assigned_to"])

        # só Raio / sem site
        lead_class = (company.get("lead_class") or "").lower()
        website = (company.get("website") or "").strip()
        wstatus = (company.get("website_status") or "").lower()
        is_raio = (
            lead_class == "raio"
            or not website
            or wstatus in ("sem_site", "so_social")
        )
        if not is_raio:
            cur.close()
            conn.close()
            return None

        cur.execute(
            """
            SELECT id, username, monthly_quota, cities, niches, active
            FROM app_users
            WHERE active = 1 AND role = 'client'
            ORDER BY id;
            """
        )
        clients = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()

        if not clients:
            logger.warning("[Users] Nenhum client ativo para receber lead.")
            return None

        candidates: list[tuple[int, int, str]] = []  # (assigned_count, user_id, username)
        for u in clients:
            u["cities"] = _parse_json_list(u.get("cities"))
            u["niches"] = _parse_json_list(u.get("niches"))
            if not _user_accepts_lead(u, company):
                continue
            used = count_assigned_this_month(int(u["id"]))
            quota = int(u.get("monthly_quota") or 0)
            if used >= quota:
                continue
            candidates.append((used, int(u["id"]), u["username"]))

        if not candidates:
            logger.warning(
                "[Users] Lead %s sem dono: cotas cheias ou filtro cidade/nicho.",
                company_id,
            )
            return None

        candidates.sort(key=lambda x: (x[0], x[1]))  # menos leads no mês primeiro
        _, user_id, uname = candidates[0]
        now = datetime.now().isoformat()

        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE companies
            SET assigned_to = %s, assigned_at = %s
            WHERE id = %s AND (assigned_to IS NULL);
            """,
            (user_id, now, company_id),
        )
        conn.commit()
        cur.close()
        conn.close()

        logger.warning(
            "[Users] Lead %s (%r) → %s (id=%s)",
            company_id,
            company.get("name"),
            uname,
            user_id,
        )
        return user_id
    except Exception as exc:
        logger.warning("[Users] assign_raio_lead falhou: %s", exc)
        return None


def manual_assign(company_id: int, user_id: int | None) -> bool:
    """Admin atribui/remove dono do lead manualmente."""
    ensure_schema()
    conn = _connect()
    try:
        cur = conn.cursor()
        if user_id:
            cur.execute(
                """
                UPDATE companies SET assigned_to = %s, assigned_at = %s WHERE id = %s;
                """,
                (user_id, datetime.now().isoformat(), company_id),
            )
        else:
            cur.execute(
                """
                UPDATE companies SET assigned_to = NULL, assigned_at = NULL WHERE id = %s;
                """,
                (company_id,),
            )
        conn.commit()
        cur.close()
        return True
    except Exception as exc:
        logger.warning("[Users] manual_assign: %s", exc)
        return False
    finally:
        conn.close()
