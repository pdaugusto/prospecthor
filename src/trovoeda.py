"""
trovoeda.py — Moeda do SaaS MVP (Trovoeda).

Fase 0: saldo + extrato (ledger) + crédito/débito admin.
Sem Stripe e sem pedidos de lead ainda.

1 Trovoeda = 1 lead (regra de produto; consumo vem nas fases seguintes).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("trovoeda")

_DATABASE_URL = os.getenv("DATABASE_URL", "")

# reasons do ledger
REASON_ADMIN_GRANT = "admin_grant"
REASON_ADMIN_DEBIT = "admin_debit"
REASON_PURCHASE = "purchase"  # futuro Stripe
REASON_RESERVE = "reserve"  # futuro pedido
REASON_RELEASE = "release"  # futuro recusa/cancelamento
REASON_SPEND = "spend"  # futuro entrega


def _connect():
    if not _DATABASE_URL:
        raise RuntimeError("DATABASE_URL não configurada")
    return psycopg2.connect(_DATABASE_URL)


def ensure_schema() -> None:
    """Colunas em app_users + tabelas Trovoeda (idempotente)."""
    if not _DATABASE_URL:
        return
    conn = _connect()
    try:
        cur = conn.cursor()
        # colunas em app_users
        for col, ddl in (
            ("email", "ALTER TABLE app_users ADD COLUMN email TEXT;"),
            ("display_name", "ALTER TABLE app_users ADD COLUMN display_name TEXT DEFAULT '';"),
            ("whatsapp", "ALTER TABLE app_users ADD COLUMN whatsapp TEXT DEFAULT '';"),
            ("trovoedas_balance", "ALTER TABLE app_users ADD COLUMN trovoedas_balance INTEGER NOT NULL DEFAULT 0;"),
            ("terms_accepted_at", "ALTER TABLE app_users ADD COLUMN terms_accepted_at TEXT;"),
            ("stripe_customer_id", "ALTER TABLE app_users ADD COLUMN stripe_customer_id TEXT;"),
        ):
            cur.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'app_users' AND column_name = %s;
                """,
                (col,),
            )
            if not cur.fetchone():
                try:
                    cur.execute(ddl)
                except Exception as exc:
                    logger.warning("[Trovoeda] add column %s: %s", col, exc)

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trovoeda_ledger (
                id              SERIAL PRIMARY KEY,
                user_id         INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
                delta           INTEGER NOT NULL,
                reason          TEXT NOT NULL,
                ref_type        TEXT DEFAULT '',
                ref_id          TEXT DEFAULT '',
                balance_after   INTEGER NOT NULL DEFAULT 0,
                note            TEXT DEFAULT '',
                created_by      INTEGER,
                created_at      TEXT NOT NULL DEFAULT (NOW()::TEXT)
            );
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_trovoeda_ledger_user ON trovoeda_ledger (user_id, id DESC);"
        )

        # pedidos de lead (cliente → host)
        try:
            from src.orders import ensure_schema as _orders_schema
            _orders_schema()
        except Exception as _ox:
            logger.warning("[Trovoeda] orders schema: %s", _ox)

        # pacotes (preço futuro Stripe) — seed opcional
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trovoeda_packages (
                id              SERIAL PRIMARY KEY,
                slug            TEXT UNIQUE NOT NULL,
                name            TEXT NOT NULL,
                coins           INTEGER NOT NULL,
                price_cents     INTEGER NOT NULL DEFAULT 0,
                stripe_price_id TEXT DEFAULT '',
                active          INTEGER NOT NULL DEFAULT 1,
                sort_order      INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        # Pacotes acessíveis — nomes clássicos: Faísca · Raio · Tempestade · Trovão
        packages_seed = [
            ("faisca", "Faísca", 10, 2900, 1),          # R$ 29
            ("raio", "Raio", 25, 5900, 2),              # R$ 59
            ("tempestade", "Tempestade", 50, 9900, 3), # R$ 99 · popular
            ("trovao", "Trovão", 100, 16900, 4),       # R$ 169
        ]
        for slug, name, coins, cents, order in packages_seed:
            cur.execute(
                """
                INSERT INTO trovoeda_packages (slug, name, coins, price_cents, sort_order, active)
                VALUES (%s, %s, %s, %s, %s, 1)
                ON CONFLICT (slug) DO UPDATE SET
                    name = EXCLUDED.name,
                    coins = EXCLUDED.coins,
                    price_cents = EXCLUDED.price_cents,
                    sort_order = EXCLUDED.sort_order,
                    active = 1;
                """,
                (slug, name, coins, cents, order),
            )
        # desativa nomes fora do catálogo atual (ex.: chispa)
        cur.execute(
            """
            UPDATE trovoeda_packages
            SET active = 0
            WHERE slug NOT IN ('faisca', 'raio', 'tempestade', 'trovao');
            """
        )

        conn.commit()
        cur.close()
    except Exception as exc:
        logger.warning("[Trovoeda] ensure_schema: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


def get_balance(user_id: int) -> int:
    if not user_id or not _DATABASE_URL:
        return 0
    try:
        ensure_schema()
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT COALESCE(trovoedas_balance, 0) FROM app_users WHERE id = %s;",
            (int(user_id),),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0


def apply_delta(
    user_id: int,
    delta: int,
    *,
    reason: str,
    note: str = "",
    ref_type: str = "",
    ref_id: str = "",
    created_by: int | None = None,
    allow_negative: bool = False,
) -> dict[str, Any]:
    """
    Aplica delta ao saldo de forma atômica.
    Returns: { ok, balance, delta, error? }
    """
    if not user_id or not _DATABASE_URL:
        return {"ok": False, "balance": 0, "delta": 0, "error": "user_id inválido"}
    delta = int(delta)
    if delta == 0:
        bal = get_balance(user_id)
        return {"ok": True, "balance": bal, "delta": 0}

    ensure_schema()
    conn = None
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("BEGIN")
        cur.execute(
            "SELECT COALESCE(trovoedas_balance, 0) FROM app_users WHERE id = %s FOR UPDATE;",
            (int(user_id),),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            cur.close()
            conn.close()
            return {"ok": False, "balance": 0, "delta": 0, "error": "usuário não encontrado"}

        bal = int(row[0] or 0)
        new_bal = bal + delta
        if new_bal < 0 and not allow_negative:
            conn.rollback()
            cur.close()
            conn.close()
            return {
                "ok": False,
                "balance": bal,
                "delta": 0,
                "error": f"saldo insuficiente ({bal})",
            }

        cur.execute(
            "UPDATE app_users SET trovoedas_balance = %s WHERE id = %s;",
            (new_bal, int(user_id)),
        )
        cur.execute(
            """
            INSERT INTO trovoeda_ledger
                (user_id, delta, reason, ref_type, ref_id, balance_after, note, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                int(user_id),
                delta,
                (reason or "adjust")[:64],
                (ref_type or "")[:64],
                (ref_id or "")[:128],
                new_bal,
                (note or "")[:500],
                int(created_by) if created_by else None,
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
        logger.info(
            "[Trovoeda] user=%s delta=%s → %s reason=%s",
            user_id,
            delta,
            new_bal,
            reason,
        )
        return {"ok": True, "balance": new_bal, "delta": delta}
    except Exception as exc:
        logger.warning("[Trovoeda] apply_delta: %s", exc)
        try:
            if conn:
                conn.rollback()
                conn.close()
        except Exception:
            pass
        return {"ok": False, "balance": get_balance(user_id), "delta": 0, "error": str(exc)}


def admin_grant(
    user_id: int,
    amount: int,
    *,
    created_by: int | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Patrão credita Trovoedas (amount > 0)."""
    amount = int(amount or 0)
    if amount <= 0:
        return {"ok": False, "balance": get_balance(user_id), "delta": 0, "error": "amount deve ser > 0"}
    return apply_delta(
        user_id,
        amount,
        reason=REASON_ADMIN_GRANT,
        note=note or "crédito manual do Patrão",
        created_by=created_by,
    )


# Bônus de boas-vindas no cadastro (MVP)
WELCOME_BONUS = int(os.getenv("TROVOEDA_WELCOME_BONUS") or "5")


def grant_welcome_bonus(user_id: int) -> dict[str, Any]:
    """5 Trovoedas grátis no primeiro cadastro (configurável)."""
    n = max(0, WELCOME_BONUS)
    if n <= 0 or not user_id:
        return {"ok": True, "balance": get_balance(user_id), "delta": 0}
    # evita double grant
    try:
        ensure_schema()
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1 FROM trovoeda_ledger
            WHERE user_id = %s AND reason = %s
            LIMIT 1;
            """,
            (int(user_id), "welcome_bonus"),
        )
        if cur.fetchone():
            cur.close()
            conn.close()
            return {"ok": True, "balance": get_balance(user_id), "delta": 0, "skipped": True}
        cur.close()
        conn.close()
    except Exception:
        pass
    return apply_delta(
        user_id,
        n,
        reason="welcome_bonus",
        note=f"Bônus de boas-vindas · {n} Trovoedas",
        ref_type="register",
    )


def spend_for_lead(
    user_id: int,
    *,
    company_id: int | None = None,
    note: str = "",
) -> dict[str, Any]:
    """
    Debita 1 Trovoeda quando um lead é entregue (assign).
    Returns ok=False se saldo insuficiente.
    """
    if not user_id:
        return {"ok": False, "balance": 0, "delta": 0, "error": "user_id inválido"}
    return apply_delta(
        int(user_id),
        -1,
        reason=REASON_SPEND,
        note=note or "lead entregue",
        ref_type="company",
        ref_id=str(company_id or ""),
    )


def public_stats() -> dict[str, Any]:
    """Números pra landing (prova social)."""
    out = {
        "leads_total": 0,
        "leads_raio": 0,
        "cities": 0,
        "niches": 0,
    }
    if not _DATABASE_URL:
        return out
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM companies;")
        out["leads_total"] = int(cur.fetchone()[0] or 0)
        cur.execute(
            """
            SELECT COUNT(*) FROM companies
            WHERE lead_class = 'raio'
               OR website_status IN ('sem_site', 'so_social')
               OR website IS NULL
               OR TRIM(COALESCE(website, '')) = '';
            """
        )
        out["leads_raio"] = int(cur.fetchone()[0] or 0)
        cur.execute(
            "SELECT COUNT(DISTINCT city) FROM companies WHERE city IS NOT NULL AND TRIM(city) <> '';"
        )
        out["cities"] = int(cur.fetchone()[0] or 0)
        cur.execute(
            "SELECT COUNT(DISTINCT niche) FROM companies WHERE niche IS NOT NULL AND TRIM(niche) <> '';"
        )
        out["niches"] = int(cur.fetchone()[0] or 0)
        cur.close()
        conn.close()
    except Exception as exc:
        logger.warning("[Trovoeda] public_stats: %s", exc)
    return out


def admin_debit(
    user_id: int,
    amount: int,
    *,
    created_by: int | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Patrão remove Trovoedas (amount > 0 → debita)."""
    amount = int(amount or 0)
    if amount <= 0:
        return {"ok": False, "balance": get_balance(user_id), "delta": 0, "error": "amount deve ser > 0"}
    return apply_delta(
        user_id,
        -amount,
        reason=REASON_ADMIN_DEBIT,
        note=note or "débito manual do Patrão",
        created_by=created_by,
    )


def list_ledger(user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    if not user_id or not _DATABASE_URL:
        return []
    try:
        ensure_schema()
        conn = _connect()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT id, user_id, delta, reason, ref_type, ref_id, balance_after, note, created_by, created_at
            FROM trovoeda_ledger
            WHERE user_id = %s
            ORDER BY id DESC
            LIMIT %s;
            """,
            (int(user_id), max(1, min(int(limit or 50), 200))),
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return rows
    except Exception as exc:
        logger.warning("[Trovoeda] list_ledger: %s", exc)
        return []


def list_packages(active_only: bool = True) -> list[dict[str, Any]]:
    if not _DATABASE_URL:
        return []
    try:
        ensure_schema()
        conn = _connect()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if active_only:
            cur.execute(
                """
                SELECT id, slug, name, coins, price_cents, stripe_price_id, active, sort_order
                FROM trovoeda_packages
                WHERE active = 1
                ORDER BY sort_order, id;
                """
            )
        else:
            cur.execute(
                """
                SELECT id, slug, name, coins, price_cents, stripe_price_id, active, sort_order
                FROM trovoeda_packages
                ORDER BY sort_order, id;
                """
            )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return rows
    except Exception:
        return []
