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
import re
from datetime import datetime
from typing import Any

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("users")

_DATABASE_URL = os.getenv("DATABASE_URL", "")
# Login do Patrão (fixo). Nunca use "admin" aqui — admin é a conta do amigo.
_PRINCIPAL_USERNAME = "patrao"
_ENV_ADMIN_USER = os.getenv("DASHBOARD_USER", "patrao")
# NUNCA default de senha no código — só env
_ENV_ADMIN_PASS = (os.getenv("DASHBOARD_PASS") or "").strip()


def _hash_password(password: str) -> str:
    """Hash moderno (pbkdf2). Preferir verify_password para login."""
    try:
        from werkzeug.security import generate_password_hash
        return generate_password_hash(password or "", method="pbkdf2:sha256", salt_length=16)
    except Exception:
        # fallback extremo (não ideal)
        return "sha256:" + hashlib.sha256((password or "").encode("utf-8")).hexdigest()


def _legacy_sha256(password: str) -> str:
    return hashlib.sha256((password or "").encode("utf-8")).hexdigest()


def verify_password(password: str, stored_hash: str) -> bool:
    """Aceita hash werkzeug (pbkdf2/scrypt) ou SHA256 legado (64 hex)."""
    stored = (stored_hash or "").strip()
    if not stored:
        return False
    # Werkzeug / pbkdf2
    if stored.startswith(("pbkdf2:", "scrypt:", "argon2:")) or stored.count("$") >= 2:
        try:
            from werkzeug.security import check_password_hash
            return check_password_hash(stored, password or "")
        except Exception:
            return False
    # Legado: sha256 hex puro
    if re.fullmatch(r"[0-9a-f]{64}", stored):
        return _legacy_sha256(password) == stored
    # Prefixo nosso
    if stored.startswith("sha256:"):
        return _legacy_sha256(password) == stored[7:]
    try:
        from werkzeug.security import check_password_hash
        return check_password_hash(stored, password or "")
    except Exception:
        return False


def _upgrade_password_hash(user_id: int, password: str) -> None:
    """Migra hash legado → pbkdf2 no login bem-sucedido."""
    if not user_id:
        return
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "UPDATE app_users SET password_hash = %s WHERE id = %s;",
            (_hash_password(password), int(user_id)),
        )
        conn.commit()
        cur.close()
        conn.close()
        logger.warning("[Users] password hash upgraded user_id=%s", user_id)
    except Exception as exc:
        logger.warning("[Users] upgrade hash falhou: %s", exc)


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

        # IP do cadastro público (anti multi-conta)
        cur.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'app_users' AND column_name = 'signup_ip'
            """
        )
        if not cur.fetchone():
            cur.execute("ALTER TABLE app_users ADD COLUMN signup_ip TEXT DEFAULT '';")
        # 1 conta pública por IP (parcial: IPs vazios do painel admin podem repetir)
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_app_users_signup_ip_unique
            ON app_users (signup_ip)
            WHERE signup_ip IS NOT NULL AND btrim(signup_ip) <> '';
            """
        )

        # Admin principal FIXO = patrao (nunca o username "admin" do amigo)
        principal = _PRINCIPAL_USERNAME
        cur.execute(
            "SELECT id FROM app_users WHERE lower(username) = %s LIMIT 1;",
            (principal,),
        )
        if not cur.fetchone():
            if not _ENV_ADMIN_PASS:
                logger.warning(
                    "[Users] DASHBOARD_PASS não definida — não criou usuário patrao. "
                    "Configure a env na Vercel/.env"
                )
            else:
                cur.execute(
                    """
                    INSERT INTO app_users (username, password_hash, role, monthly_quota, active, label)
                    VALUES (%s, %s, 'admin', 9999, 1, 'Patrão')
                    """,
                    (principal, _hash_password(_ENV_ADMIN_PASS)),
                )
                logger.warning("[Users] Admin principal criado: %s", principal)
        else:
            # Só garante role/admin do patrao — NÃO sobrescreve label se já customizado
            cur.execute(
                """
                UPDATE app_users
                SET role = 'admin', active = 1, monthly_quota = 9999
                WHERE lower(username) = %s;
                """,
                (principal,),
            )
            cur.execute(
                """
                UPDATE app_users
                SET label = 'Patrão'
                WHERE lower(username) = %s
                  AND (label IS NULL OR TRIM(label) = '' OR label IN ('Administrador'));
                """,
                (principal,),
            )
        # Conta "admin" e qualquer outro NÃO-patrao nunca é admin do sistema
        cur.execute(
            """
            UPDATE app_users
            SET role = 'client',
                monthly_quota = CASE WHEN monthly_quota >= 9999 THEN 100 ELSE monthly_quota END
            WHERE lower(username) <> %s AND role = 'admin';
            """,
            (principal,),
        )
        # Se o amigo "admin" ficou com label errado "Patrão", corrige uma vez
        cur.execute(
            """
            UPDATE app_users
            SET label = 'Amigo'
            WHERE lower(username) = 'admin' AND label = 'Patrão';
            """
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()
    # Trovoeda (saldo SaaS) — não quebra se falhar
    try:
        from src.trovoeda import ensure_schema as _trovoeda_schema
        _trovoeda_schema()
    except Exception as exc:
        logger.warning("[Users] trovoeda schema: %s", exc)


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    """Valida login por hash no banco. Fallback DASHBOARD_PASS só para patrao (env)."""
    username = (username or "").strip()
    password = password or ""
    if not username or not password:
        return None

    uname_l = username.lower()

    try:
        ensure_schema()
        conn = _connect()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT id, username, role, monthly_quota, active, cities, niches, label, password_hash
            FROM app_users
            WHERE lower(username) = lower(%s)
            LIMIT 1;
            """,
            (username,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()

        if row:
            user = dict(row)
            stored = user.pop("password_hash", "") or ""
            # active = só "recebe leads" (distribuição/bot). NÃO bloqueia login.
            if not verify_password(password, stored):
                # Fallback: patrao ainda com senha só na env (migração)
                if (
                    uname_l == _PRINCIPAL_USERNAME
                    and _ENV_ADMIN_PASS
                    and password == _ENV_ADMIN_PASS
                ):
                    _upgrade_password_hash(int(user["id"]), password)
                    user["role"] = "admin"
                    user["cities"] = _parse_json_list(user.get("cities"))
                    user["niches"] = _parse_json_list(user.get("niches"))
                    return user
                return None
            # migra SHA256 legado → pbkdf2
            if re.fullmatch(r"[0-9a-f]{64}", (stored or "").strip()) or (
                stored or ""
            ).startswith("sha256:"):
                _upgrade_password_hash(int(user["id"]), password)
            if uname_l == _PRINCIPAL_USERNAME:
                user["role"] = "admin"
            user["cities"] = _parse_json_list(user.get("cities"))
            user["niches"] = _parse_json_list(user.get("niches"))
            return user

        # patrao ainda não existe no banco: bootstrap só com env
        if (
            uname_l == _PRINCIPAL_USERNAME
            and _ENV_ADMIN_PASS
            and password == _ENV_ADMIN_PASS
        ):
            return {
                "id": 0,
                "username": _PRINCIPAL_USERNAME,
                "role": "admin",
                "monthly_quota": 9999,
                "active": 1,
                "label": "Patrão",
                "cities": [],
                "niches": [],
            }
        return None
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
    try:
        ensure_schema()
    except Exception:
        pass
    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT id, username, role, monthly_quota, active, cities, niches, label,
                   COALESCE(trovoedas_balance, 0) AS trovoedas_balance,
                   COALESCE(email, '') AS email,
                   COALESCE(display_name, '') AS display_name
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
        u["trovoedas_balance"] = int(u.get("trovoedas_balance") or 0)
        return u
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    if not user_id:
        return None
    try:
        ensure_schema()
    except Exception:
        pass
    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT id, username, role, monthly_quota, active, cities, niches, label,
                   COALESCE(trovoedas_balance, 0) AS trovoedas_balance,
                   COALESCE(email, '') AS email,
                   COALESCE(display_name, '') AS display_name
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
        u["trovoedas_balance"] = int(u.get("trovoedas_balance") or 0)
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
            SELECT id, username, role, monthly_quota, active, cities, niches, label, created_at,
                   COALESCE(trovoedas_balance, 0) AS trovoedas_balance,
                   COALESCE(email, '') AS email,
                   COALESCE(display_name, '') AS display_name
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
            u["trovoedas_balance"] = int(u.get("trovoedas_balance") or 0)
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


def normalize_signup_ip(ip: str | None) -> str:
    """Normaliza IP de cadastro; vazio se inválido/desconhecido."""
    s = (ip or "").strip()
    if not s or s.lower() in ("unknown", "null", "none", "0.0.0.0", "::"):
        return ""
    # só o primeiro hop (já costuma vir limpo do X-Forwarded-For)
    s = s.split(",")[0].strip()
    # tamanho razoável (IPv4 / IPv6)
    if len(s) > 64:
        s = s[:64]
    return s


def count_users_by_signup_ip(ip: str) -> int:
    """Quantas contas já foram criadas com este IP (cadastro público)."""
    ip_n = normalize_signup_ip(ip)
    if not ip_n or not _DATABASE_URL:
        return 0
    try:
        ensure_schema()
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COUNT(*) FROM app_users
                WHERE signup_ip IS NOT NULL
                  AND signup_ip <> ''
                  AND signup_ip = %s;
                """,
                (ip_n,),
            )
            n = cur.fetchone()[0]
            cur.close()
            return int(n or 0)
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("[Users] count_users_by_signup_ip: %s", exc)
        return 0


def create_user(
    username: str,
    password: str,
    monthly_quota: int = 50,
    role: str = "client",
    cities: list[str] | None = None,
    niches: list[str] | None = None,
    label: str = "",
    email: str = "",
    whatsapp: str = "",
    display_name: str = "",
    terms_accepted: bool = False,
    welcome_bonus: bool = False,
    signup_ip: str = "",
    enforce_ip_limit: bool = False,
) -> dict[str, Any]:
    ensure_schema()
    username = (username or "").strip().lower()
    if not username or not password:
        raise ValueError("username e password obrigatórios")
    # Nunca criar outro admin pelo painel — só "patrao" é admin do sistema
    if username == _PRINCIPAL_USERNAME:
        role = "admin"
    else:
        role = "client"
    email_n = (email or "").strip().lower()
    wa = re.sub(r"\D", "", str(whatsapp or ""))
    dname = (display_name or label or username).strip()
    terms_at = datetime.now().isoformat() if terms_accepted else None
    ip_n = normalize_signup_ip(signup_ip)

    # Cadastro público: 1 conta por IP (evita farm de bônus / multi-conta)
    if enforce_ip_limit and ip_n:
        existing = count_users_by_signup_ip(ip_n)
        if existing > 0:
            raise ValueError(
                "Já existe uma conta criada nesta rede/dispositivo. "
                "Entre com a conta existente ou fale com o suporte se precisar de ajuda."
            )

    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            INSERT INTO app_users
                (username, password_hash, role, monthly_quota, active, cities, niches, label,
                 email, whatsapp, display_name, terms_accepted_at, signup_ip)
            VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, username, role, monthly_quota, active, cities, niches, label,
                      COALESCE(trovoedas_balance, 0) AS trovoedas_balance,
                      COALESCE(email, '') AS email;
            """,
            (
                username,
                _hash_password(password),
                role,
                int(monthly_quota or 50),
                json.dumps(cities or [], ensure_ascii=False),
                json.dumps(niches or [], ensure_ascii=False),
                label or dname or username,
                email_n or None,
                wa or "",
                dname,
                terms_at,
                ip_n or "",
            ),
        )
        row = dict(cur.fetchone())
        conn.commit()
        cur.close()
        row["cities"] = cities or []
        row["niches"] = niches or []
        row["assigned_this_month"] = 0
        row["trovoedas_balance"] = int(row.get("trovoedas_balance") or 0)
        logger.warning("[Users] Criado: %s quota=%s", username, monthly_quota)
        if welcome_bonus and role == "client":
            try:
                from src.trovoeda import grant_welcome_bonus
                bonus = grant_welcome_bonus(int(row["id"]))
                row["trovoedas_balance"] = int(bonus.get("balance") or 0)
                row["welcome_bonus"] = int(bonus.get("delta") or 0)
            except Exception as exc:
                logger.warning("[Users] welcome bonus: %s", exc)
        return row
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_user(user_id: int, **fields: Any) -> dict[str, Any] | None:
    # Não chama ensure_schema aqui (evita side-effects em labels)
    target = get_user_by_id(user_id)
    if not target:
        return None
    if (target.get("username") or "").lower() == "patrao":
        # Patrão: só senha/label
        fields = {
            k: v
            for k, v in fields.items()
            if k in ("password", "label")
        }

    sets = []
    params: list[Any] = []

    if "label" in fields and fields["label"] is not None:
        sets.append("label = %s")
        params.append(str(fields["label"]).strip())

    if "password" in fields and str(fields.get("password") or "").strip():
        sets.append("password_hash = %s")
        params.append(_hash_password(str(fields["password"])))

    if "username" in fields and fields["username"] is not None:
        new_u = str(fields["username"]).strip().lower()
        if (
            new_u
            and new_u != "patrao"
            and (target.get("username") or "").lower() != "patrao"
        ):
            sets.append("username = %s")
            params.append(new_u)

    if "monthly_quota" in fields and fields["monthly_quota"] is not None:
        try:
            sets.append("monthly_quota = %s")
            params.append(max(0, int(fields["monthly_quota"])))
        except (TypeError, ValueError):
            pass

    if "active" in fields and fields["active"] is not None:
        if (target.get("username") or "").lower() != "patrao":
            sets.append("active = %s")
            params.append(1 if fields["active"] in (True, 1, "1", "true") else 0)

    if "role" in fields and fields["role"] is not None:
        if (target.get("username") or "").lower() != "patrao":
            role = str(fields["role"]).lower()
            if role not in ("client",):
                role = "client"  # não promove ninguém a admin por aqui
            sets.append("role = %s")
            params.append(role)

    if "cities" in fields and fields["cities"] is not None:
        sets.append("cities = %s")
        params.append(
            json.dumps(
                fields["cities"] if isinstance(fields["cities"], list) else [],
                ensure_ascii=False,
            )
        )
    if "niches" in fields and fields["niches"] is not None:
        sets.append("niches = %s")
        params.append(
            json.dumps(
                fields["niches"] if isinstance(fields["niches"], list) else [],
                ensure_ascii=False,
            )
        )

    if not sets:
        return get_user_by_id(user_id)

    params.append(user_id)
    conn = _connect()
    try:
        cur = conn.cursor()
        sql = f"UPDATE app_users SET {', '.join(sets)} WHERE id = %s;"
        cur.execute(sql, params)
        conn.commit()
        cur.close()
        logger.warning("[Users] update id=%s fields=%s", user_id, list(fields.keys()))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_user_by_id(user_id)


def reset_month_usage(user_id: int) -> int:
    """
    Zera a contagem de leads 'recebidos este mês' (mantém os leads com o cliente).
    Assim a cota mensal volta a contar do zero.
    """
    ensure_schema()
    month_prefix = datetime.now().strftime("%Y-%m")
    # marca assigned_at no mês passado para não contar no mês atual
    fake_at = datetime.now().replace(day=1).isoformat()
    # usa dia 1 do mês anterior de forma simples
    y, m = datetime.now().year, datetime.now().month
    if m == 1:
        y, m = y - 1, 12
    else:
        m -= 1
    fake_at = f"{y:04d}-{m:02d}-01T00:00:00"
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE companies
            SET assigned_at = %s
            WHERE assigned_to = %s
              AND assigned_at IS NOT NULL
              AND assigned_at LIKE %s;
            """,
            (fake_at, user_id, f"{month_prefix}%"),
        )
        n = cur.rowcount
        conn.commit()
        cur.close()
        logger.warning("[Users] Uso do mês zerado user_id=%s rows=%s", user_id, n)
        return int(n or 0)
    finally:
        conn.close()


def delete_user(user_id: int, reassign_leads_to: int | None = None) -> bool:
    """
    Remove usuário. Leads dele ficam sem dono (Patrão vê) ou vão para reassign_leads_to.
    Não remove a conta patrao.
    """
    ensure_schema()
    u = get_user_by_id(user_id)
    if not u:
        return False
    if (u.get("username") or "").lower() == "patrao":
        raise ValueError("Não é permitido remover a conta Patrão.")
    conn = _connect()
    try:
        cur = conn.cursor()
        if reassign_leads_to:
            cur.execute(
                """
                UPDATE companies SET assigned_to = %s, assigned_at = %s
                WHERE assigned_to = %s;
                """,
                (reassign_leads_to, datetime.now().isoformat(), user_id),
            )
        else:
            cur.execute(
                """
                UPDATE companies SET assigned_to = NULL, assigned_at = NULL
                WHERE assigned_to = %s;
                """,
                (user_id,),
            )
        cur.execute("DELETE FROM app_users WHERE id = %s;", (user_id,))
        conn.commit()
        cur.close()
        logger.warning("[Users] Removido user_id=%s (%s)", user_id, u.get("username"))
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def has_contact_phone(phone: Any) -> bool:
    """
    Telefone utilizável para o cliente ligar/WhatsApp.
    Exige ao menos 10 dígitos (DDD + número) ou 12+ com código 55.
    """
    try:
        from src.contact import has_contact_phone as _h
        return _h(phone)
    except Exception:
        digits = re.sub(r"\D", "", str(phone or ""))
        if not digits:
            return False
        if digits.startswith("55") and len(digits) >= 12:
            return True
        return len(digits) >= 10


def lead_has_client_contact(company: dict[str, Any] | None) -> bool:
    """Tel OU Instagram — critério para mandar ao cliente / contar lead."""
    try:
        from src.contact import has_usable_contact
        return has_usable_contact(company or {})
    except Exception:
        return has_contact_phone((company or {}).get("phone"))


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


def unassign_leads_without_phone() -> int:
    """Tira de clientes qualquer lead sem telefone útil (vira sobra do Patrão)."""
    if not _DATABASE_URL:
        return 0
    ensure_schema()
    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT id, phone, assigned_to, name FROM companies
            WHERE assigned_to IS NOT NULL;
            """
        )
        rows = cur.fetchall()
        bad_ids: list[int] = []
        for r in rows:
            if not has_contact_phone(r.get("phone")):
                bad_ids.append(int(r["id"]))
        if not bad_ids:
            cur.close()
            return 0
        cur.execute(
            """
            UPDATE companies
            SET assigned_to = NULL, assigned_at = NULL
            WHERE id IN %s;
            """,
            (tuple(bad_ids),),
        )
        n = cur.rowcount
        conn.commit()
        cur.close()
        logger.warning(
            "[Users] %s lead(s) sem telefone devolvidos ao pool (não vão pra cliente)",
            n,
        )
        return int(n or 0)
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("[Users] unassign_leads_without_phone: %s", exc)
        return 0
    finally:
        conn.close()


def _is_host_user(user_id: int) -> bool:
    """Patrão / host do sistema — Trovoedas ilimitadas."""
    if not user_id or not _DATABASE_URL:
        return False
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT username, role FROM app_users WHERE id = %s LIMIT 1;",
            (int(user_id),),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return False
        uname = (row[0] or "").lower().strip()
        role = (row[1] or "").lower().strip()
        return uname == "patrao" or role == "admin"
    except Exception:
        return False


def _try_spend_trovoeda(user_id: int, company_id: int) -> None:
    """Debita 1 Trovoeda se o usuário tiver saldo (SaaS). Sem saldo = fluxo legado (cota).
    Patrão (host) tem Trovoedas infinitas — nunca debita."""
    try:
        if _is_host_user(int(user_id)):
            logger.info("[Trovoeda] skip spend host user=%s lead=%s", user_id, company_id)
            return

        from src.trovoeda import get_balance, spend_for_lead
        if get_balance(int(user_id)) <= 0:
            return
        r = spend_for_lead(int(user_id), company_id=int(company_id))
        if r.get("ok"):
            logger.info(
                "[Trovoeda] -1 lead=%s user=%s saldo=%s",
                company_id,
                user_id,
                r.get("balance"),
            )
        else:
            logger.warning("[Trovoeda] debit falhou user=%s: %s", user_id, r.get("error"))
    except Exception as exc:
        logger.warning("[Trovoeda] spend: %s", exc)


def assign_raio_lead(company_id: int) -> int | None:
    """
    Atribui lead Raio a um cliente com vaga na cota do mês.
    Round-robin justo: quem tem menos leads no mês (e ainda tem quota).
    Retorna user_id ou None se ninguém puder receber.
    Nunca atribui lead SEM telefone de contato.
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

        # precisa WA (celular) ou Instagram — fixo sozinho / gigante não vai
        try:
            from src.contact import has_usable_contact, is_giant_enterprise
            ok_contact = has_usable_contact(company)
            giant = is_giant_enterprise(company)
        except Exception:
            ok_contact = has_contact_phone(company.get("phone"))
            giant = False
        if giant:
            logger.warning(
                "[Users] Lead %s (%r) empresa GIGANTE — não atribuído",
                company_id,
                company.get("name"),
            )
            cur.close()
            conn.close()
            return None
        if not ok_contact:
            logger.warning(
                "[Users] Lead %s (%r) sem celular/WA nem Instagram — não atribuído",
                company_id,
                company.get("name"),
            )
            cur.close()
            conn.close()
            return None

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

        # Cockpit / missão: dono escolhido na mão.
        # SEMPRE atribui ao dono da missão (não bloqueia na cota mensal):
        # a meta da missão é o teto real; se a cota mensal barrasse o 20º,
        # a meta batia 20/20 no claim e o cliente ficava com 19.
        force_raw = (os.getenv("PROSPECTHOR_FORCE_ASSIGN_TO") or "").strip()
        if force_raw.isdigit():
            force_uid = int(force_raw)
            cur.execute(
                """
                SELECT id, username, monthly_quota, active, role
                FROM app_users WHERE id = %s LIMIT 1;
                """,
                (force_uid,),
            )
            fu = cur.fetchone()
            if fu:
                fu = dict(fu)
                used = count_assigned_this_month(force_uid)
                quota = int(fu.get("monthly_quota") or 0)
                cur.execute(
                    """
                    UPDATE companies SET assigned_to = %s, assigned_at = %s
                    WHERE id = %s AND (assigned_to IS NULL);
                    """,
                    (force_uid, datetime.now().isoformat(), company_id),
                )
                assigned_ok = cur.rowcount > 0
                conn.commit()
                cur.close()
                conn.close()
                if assigned_ok:
                    _try_spend_trovoeda(force_uid, company_id)
                if quota > 0 and used >= quota:
                    logger.warning(
                        "[Users] Lead %s → missão user_id=%s (%s) "
                        "MESMO com cota mensal cheia %s/%s (missão manda)",
                        company_id,
                        force_uid,
                        fu.get("username"),
                        used,
                        quota,
                    )
                else:
                    logger.info(
                        "[Users] Lead %s → missão/cockpit user_id=%s (%s)",
                        company_id,
                        force_uid,
                        fu.get("username"),
                    )
                return force_uid

        # Só clientes ativos (ex.: amigo "admin") — nunca o patrao
        cur.execute(
            """
            SELECT id, username, monthly_quota, cities, niches, active
            FROM app_users
            WHERE active = 1 AND role = 'client' AND lower(username) <> 'patrao'
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
        assigned_ok = cur.rowcount > 0
        conn.commit()
        cur.close()
        conn.close()

        if assigned_ok:
            _try_spend_trovoeda(user_id, company_id)
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
    """Admin atribui/remove dono do lead manualmente.

    Se user_id for cliente, exige telefone de contato (senão False).
    """
    ensure_schema()
    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if user_id:
            cur.execute(
                "SELECT phone, name FROM companies WHERE id = %s LIMIT 1;",
                (int(company_id),),
            )
            row = cur.fetchone()
            row_d = dict(row) if row else {}
            # tenta carregar IG do banco se existir
            try:
                cur.execute(
                    "SELECT phone, instagram_url, instagram_username, website "
                    "FROM companies WHERE id = %s LIMIT 1;",
                    (int(company_id),),
                )
                row2 = cur.fetchone()
                if row2:
                    row_d = dict(row2)
            except Exception:
                pass
            if not lead_has_client_contact(row_d):
                logger.warning(
                    "[Users] manual_assign bloqueado: lead %s sem tel/IG",
                    company_id,
                )
                cur.close()
                return False
            # só debita se estava livre e agora ganhou dono
            cur.execute("SELECT assigned_to FROM companies WHERE id = %s;", (int(company_id),))
            prev = cur.fetchone()
            if isinstance(prev, dict):
                prev_owner = prev.get("assigned_to")
            else:
                prev_owner = prev[0] if prev else None
            cur.execute(
                """
                UPDATE companies SET assigned_to = %s, assigned_at = %s WHERE id = %s;
                """,
                (user_id, datetime.now().isoformat(), company_id),
            )
            should_spend = prev_owner is None
        else:
            cur.execute(
                """
                UPDATE companies SET assigned_to = NULL, assigned_at = NULL WHERE id = %s;
                """,
                (company_id,),
            )
            should_spend = False
        conn.commit()
        cur.close()
        if user_id and should_spend:
            try:
                _try_spend_trovoeda(int(user_id), int(company_id))
            except Exception:
                pass
        return True
    except Exception as exc:
        logger.warning("[Users] manual_assign: %s", exc)
        return False
    finally:
        conn.close()


def delete_company_lead(company_id: int) -> bool:
    """Apaga o lead (empresa) do banco. Só deve ser chamado pelo Patrão na API."""
    if not _DATABASE_URL:
        return False
    ensure_schema()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM companies WHERE id = %s;", (int(company_id),))
        n = cur.rowcount
        conn.commit()
        cur.close()
        if n:
            logger.warning("[Users] Lead id=%s apagado do banco", company_id)
        return bool(n)
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("[Users] delete_company_lead: %s", exc)
        return False
    finally:
        conn.close()


def distribute_selected_leads(
    lead_ids: list[int],
    user_id: int,
    respect_quota: bool = True,
) -> dict[str, Any]:
    """
    Patrão escolhe sobras específicas e manda para UM cliente.

    - Só leads com assigned_to IS NULL
    - Exige telefone de contato
    - respect_quota=True: não fura cota mensal do destinatário
    """
    ensure_schema()
    result: dict[str, Any] = {
        "distributed": 0,
        "skipped": 0,
        "skipped_no_phone": 0,
        "skipped_not_free": 0,
        "skipped_quota": 0,
        "by_user": {},
        "message": "",
        "user_id": int(user_id),
        "remaining_free": 0,
    }
    if not _DATABASE_URL:
        result["message"] = "DATABASE_URL não configurada"
        return result

    ids: list[int] = []
    seen: set[int] = set()
    for x in lead_ids or []:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if i in seen:
            continue
        seen.add(i)
        ids.append(i)
    if not ids:
        result["message"] = "Nenhum lead selecionado."
        return result

    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT id, username, monthly_quota, cities, niches, active, label, role
            FROM app_users
            WHERE id = %s
            LIMIT 1;
            """,
            (int(user_id),),
        )
        urow = cur.fetchone()
        if not urow:
            result["message"] = "Usuário destinatário não encontrado."
            cur.close()
            return result
        user = dict(urow)
        uname = (user.get("username") or "").lower()
        if uname == "patrao" or (user.get("role") or "").lower() == "admin":
            result["message"] = "Não envie sobras para a conta do Patrão. Escolha um cliente."
            cur.close()
            return result
        if user.get("active") not in (1, True, "1"):
            result["message"] = "Cliente inativo — ative-o para receber leads."
            cur.close()
            return result

        user["cities"] = _parse_json_list(user.get("cities"))
        user["niches"] = _parse_json_list(user.get("niches"))
        used = count_assigned_this_month(int(user_id))
        quota = int(user.get("monthly_quota") or 0)
        slots = max(0, quota - used) if respect_quota else len(ids)

        try:
            cur.execute(
                """
                SELECT id, name, city, niche, phone, website, assigned_to, lead_score,
                       instagram_url, instagram_username
                FROM companies
                WHERE id = ANY(%s);
                """,
                (ids,),
            )
        except Exception:
            conn.rollback()
            cur.execute(
                """
                SELECT id, name, city, niche, phone, website, assigned_to, lead_score
                FROM companies
                WHERE id = ANY(%s);
                """,
                (ids,),
            )
        rows = {int(r["id"]): dict(r) for r in cur.fetchall()}
        now = datetime.now().isoformat()
        distributed = 0
        skipped = 0
        skipped_no_phone = 0
        skipped_not_free = 0
        skipped_quota = 0

        for lid in ids:
            company = rows.get(lid)
            if not company:
                skipped += 1
                continue
            if company.get("assigned_to") is not None:
                skipped_not_free += 1
                skipped += 1
                continue
            if not lead_has_client_contact(company):
                skipped_no_phone += 1
                skipped += 1
                continue
            if not _user_accepts_lead(user, company):
                skipped += 1
                continue
            if respect_quota and distributed >= slots:
                skipped_quota += 1
                skipped += 1
                continue

            cur.execute(
                """
                UPDATE companies
                SET assigned_to = %s, assigned_at = %s
                WHERE id = %s AND assigned_to IS NULL;
                """,
                (int(user_id), now, lid),
            )
            if cur.rowcount:
                distributed += 1
            else:
                skipped_not_free += 1
                skipped += 1

        conn.commit()

        cur.execute(
            """
            SELECT COUNT(*) AS n FROM companies
            WHERE assigned_to IS NULL
              AND (
                lead_class = 'raio'
                OR website_status IN ('sem_site', 'so_social')
                OR website IS NULL
                OR TRIM(COALESCE(website, '')) = ''
              );
            """
        )
        remaining = int((cur.fetchone() or {}).get("n") or 0)
        cur.close()

        label = user.get("label") or user.get("username") or str(user_id)
        result["distributed"] = distributed
        result["skipped"] = skipped
        result["skipped_no_phone"] = skipped_no_phone
        result["skipped_not_free"] = skipped_not_free
        result["skipped_quota"] = skipped_quota
        result["remaining_free"] = remaining
        result["by_user"] = {str(user.get("username") or label): distributed} if distributed else {}
        if distributed == 0:
            parts = []
            if skipped_no_phone:
                parts.append(f"{skipped_no_phone} sem telefone")
            if skipped_not_free:
                parts.append(f"{skipped_not_free} já tinham dono")
            if skipped_quota:
                parts.append(f"{skipped_quota} sem vaga na cota")
            if not parts:
                parts.append("nenhum lead elegível")
            result["message"] = "Nada distribuído: " + ", ".join(parts) + "."
        else:
            result["message"] = (
                f"Enviados {distributed} lead(s) para {label}."
                + (f" {skipped} ignorado(s)." if skipped else "")
                + (f" Cota: {used + distributed}/{quota}." if respect_quota else "")
            )
        return result
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("[Users] distribute_selected_leads: %s", exc)
        result["message"] = f"Erro: {exc}"
        return result
    finally:
        conn.close()


def distribute_free_leads(
    limit: int | None = None,
    only_user_id: int | None = None,
) -> dict[str, Any]:
    """
    Distribui sobras (assigned_to IS NULL) para clientes ATIVOS com vaga na cota.

    - Round-robin justo: quem tem menos leads no mês recebe primeiro
    - Respeita cota mensal e filtro cidade/nicho do usuário
    - limit: máximo de leads a distribuir (None = todos que couberem)
    - only_user_id: manda só para um cliente (ainda respeita cota dele)

    NÃO zera nada no mês novo — só empurra o pool livre agora.
    """
    ensure_schema()
    # limpa carteiras: sem telefone não fica com cliente
    freed = unassign_leads_without_phone()
    result: dict[str, Any] = {
        "distributed": 0,
        "remaining_free": 0,
        "skipped": 0,
        "by_user": {},
        "freed_no_phone": freed,
        "message": "",
    }
    if not _DATABASE_URL:
        result["message"] = "DATABASE_URL não configurada"
        return result

    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Clientes elegíveis
        if only_user_id:
            cur.execute(
                """
                SELECT id, username, monthly_quota, cities, niches, active, label
                FROM app_users
                WHERE id = %s AND lower(username) <> 'patrao'
                LIMIT 1;
                """,
                (int(only_user_id),),
            )
            clients = [dict(r) for r in cur.fetchall()]
            clients = [
                u for u in clients
                if u.get("active") in (1, True, "1")
            ]
        else:
            cur.execute(
                """
                SELECT id, username, monthly_quota, cities, niches, active, label
                FROM app_users
                WHERE active = 1 AND role = 'client' AND lower(username) <> 'patrao'
                ORDER BY id;
                """
            )
            clients = [dict(r) for r in cur.fetchall()]

        if not clients:
            # conta sobras mesmo assim
            cur.execute(
                """
                SELECT COUNT(*) AS n FROM companies
                WHERE assigned_to IS NULL
                  AND (
                    lead_class = 'raio'
                    OR website_status IN ('sem_site', 'so_social')
                    OR website IS NULL
                    OR TRIM(COALESCE(website, '')) = ''
                  );
                """
            )
            result["remaining_free"] = int((cur.fetchone() or {}).get("n") or 0)
            result["message"] = "Nenhum cliente ATIVO com vaga para receber."
            cur.close()
            return result

        for u in clients:
            u["cities"] = _parse_json_list(u.get("cities"))
            u["niches"] = _parse_json_list(u.get("niches"))
            u["_used"] = count_assigned_this_month(int(u["id"]))
            u["_quota"] = int(u.get("monthly_quota") or 0)

        # Sobras (raio / sem site), melhor score primeiro
        cur.execute(
            """
            SELECT id, name, city, niche, phone, lead_score, assigned_to,
                   website, website_status, lead_class,
                   instagram_url, instagram_username
            FROM companies
            WHERE assigned_to IS NULL
              AND (
                lead_class = 'raio'
                OR website_status IN ('sem_site', 'so_social')
                OR website IS NULL
                OR TRIM(COALESCE(website, '')) = ''
              )
            ORDER BY lead_score DESC NULLS LAST, id ASC;
            """
        )
        free_leads = [dict(r) for r in cur.fetchall()]
        if limit is not None:
            try:
                lim = max(0, int(limit))
                free_leads = free_leads[:lim]
            except (TypeError, ValueError):
                pass

        if not free_leads:
            result["message"] = "Nenhuma sobra para distribuir."
            cur.close()
            return result

        by_user: dict[str, int] = {}
        distributed = 0
        skipped = 0
        now = datetime.now().isoformat()

        for company in free_leads:
            # Cliente não recebe lead sem tel/IG
            if not lead_has_client_contact(company):
                skipped += 1
                continue
            candidates: list[tuple[int, dict]] = []
            for u in clients:
                if u["_quota"] <= 0 or u["_used"] >= u["_quota"]:
                    continue
                if not _user_accepts_lead(u, company):
                    continue
                candidates.append((u["_used"], u))
            if not candidates:
                skipped += 1
                continue
            candidates.sort(key=lambda x: (x[0], int(x[1]["id"])))
            chosen = candidates[0][1]
            uid = int(chosen["id"])
            uname = chosen.get("username") or str(uid)

            cur.execute(
                """
                UPDATE companies
                SET assigned_to = %s, assigned_at = %s
                WHERE id = %s AND assigned_to IS NULL;
                """,
                (uid, now, int(company["id"])),
            )
            if cur.rowcount:
                chosen["_used"] += 1
                distributed += 1
                by_user[uname] = by_user.get(uname, 0) + 1
            else:
                skipped += 1

        conn.commit()

        # sobras que sobraram no banco
        cur.execute(
            """
            SELECT COUNT(*) AS n FROM companies
            WHERE assigned_to IS NULL
              AND (
                lead_class = 'raio'
                OR website_status IN ('sem_site', 'so_social')
                OR website IS NULL
                OR TRIM(COALESCE(website, '')) = ''
              );
            """
        )
        remaining = int((cur.fetchone() or {}).get("n") or 0)
        cur.close()

        result["distributed"] = distributed
        result["remaining_free"] = remaining
        result["skipped"] = skipped
        result["by_user"] = by_user
        if distributed == 0:
            result["message"] = (
                "Nada distribuído: cotas cheias, filtros cidade/nicho ou sem cliente ATIVO."
            )
        else:
            parts = [f"{k}: {v}" for k, v in by_user.items()]
            result["message"] = (
                f"Distribuiu {distributed} sobra(s)"
                + (f" ({', '.join(parts)})" if parts else "")
                + f". Restam {remaining} livres."
            )
        logger.warning("[Users] distribute_free_leads: %s", result["message"])
        return result
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("[Users] distribute_free_leads falhou: %s", exc)
        result["message"] = str(exc)
        return result
    finally:
        try:
            conn.close()
        except Exception:
            pass
