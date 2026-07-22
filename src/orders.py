"""
orders.py — Pedidos de leads (cliente pede → host aprova/recusa → entrega).

Fluxo:
1. Cliente cria pedido de N leads (precisa saldo ≥ N).
2. N Trovoedas são reservadas (débito imediato no saldo).
3. Host (Patrão) aprova ou recusa.
   - Recusa → estorna as moedas.
   - Aprova → tenta entregar sobras do pool (sem cobrar de novo).
4. Host pode clicar “Entregar mais” enquanto delivered < quantity.
5. Quando delivered ≥ quantity → status fulfilled.
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
logger = logging.getLogger("orders")

_DATABASE_URL = os.getenv("DATABASE_URL", "")

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_FULFILLED = "fulfilled"
STATUS_CANCELLED = "cancelled"

_VALID_STATUS = {
    STATUS_PENDING,
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_FULFILLED,
    STATUS_CANCELLED,
}


def _connect():
    if not _DATABASE_URL:
        raise RuntimeError("DATABASE_URL não configurada")
    return psycopg2.connect(_DATABASE_URL)


def ensure_schema() -> None:
    if not _DATABASE_URL:
        return
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS lead_orders (
                id              SERIAL PRIMARY KEY,
                user_id         INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
                quantity        INTEGER NOT NULL,
                delivered       INTEGER NOT NULL DEFAULT 0,
                reserved        INTEGER NOT NULL DEFAULT 0,
                niche           TEXT DEFAULT '',
                city            TEXT DEFAULT '',
                notes           TEXT DEFAULT '',
                status          TEXT NOT NULL DEFAULT 'pending',
                host_note       TEXT DEFAULT '',
                reviewed_by     INTEGER,
                reviewed_at     TEXT,
                created_at      TEXT NOT NULL DEFAULT (NOW()::TEXT),
                updated_at      TEXT NOT NULL DEFAULT (NOW()::TEXT)
            );
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_lead_orders_user ON lead_orders (user_id, id DESC);"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_lead_orders_status ON lead_orders (status, id DESC);"
        )
        conn.commit()
        cur.close()
    except Exception as exc:
        logger.warning("[Orders] ensure_schema: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


def _now() -> str:
    return datetime.now().isoformat()


def _row_to_dict(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    d = dict(row)
    for k in ("id", "user_id", "quantity", "delivered", "reserved", "reviewed_by"):
        if d.get(k) is not None:
            try:
                d[k] = int(d[k])
            except (TypeError, ValueError):
                pass
    d["remaining"] = max(0, int(d.get("quantity") or 0) - int(d.get("delivered") or 0))
    return d


def get_order(order_id: int) -> dict[str, Any] | None:
    if not order_id or not _DATABASE_URL:
        return None
    ensure_schema()
    try:
        conn = _connect()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT o.*,
                   u.username AS username,
                   COALESCE(u.label, u.username) AS user_label
            FROM lead_orders o
            LEFT JOIN app_users u ON u.id = o.user_id
            WHERE o.id = %s
            LIMIT 1;
            """,
            (int(order_id),),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        return _row_to_dict(row) if row else None
    except Exception as exc:
        logger.warning("[Orders] get_order: %s", exc)
        return None


def list_orders(
    *,
    user_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if not _DATABASE_URL:
        return []
    ensure_schema()
    try:
        conn = _connect()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        sql = """
            SELECT o.*,
                   u.username AS username,
                   COALESCE(u.label, u.username) AS user_label
            FROM lead_orders o
            LEFT JOIN app_users u ON u.id = o.user_id
            WHERE 1=1
        """
        params: list[Any] = []
        if user_id:
            sql += " AND o.user_id = %s"
            params.append(int(user_id))
        if status and status in _VALID_STATUS:
            sql += " AND o.status = %s"
            params.append(status)
        sql += " ORDER BY o.id DESC LIMIT %s"
        params.append(max(1, min(int(limit or 50), 200)))
        cur.execute(sql, params)
        rows = [_row_to_dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return rows
    except Exception as exc:
        logger.warning("[Orders] list_orders: %s", exc)
        return []


def count_pending() -> int:
    if not _DATABASE_URL:
        return 0
    ensure_schema()
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM lead_orders WHERE status = %s;",
            (STATUS_PENDING,),
        )
        n = int((cur.fetchone() or [0])[0] or 0)
        cur.close()
        conn.close()
        return n
    except Exception:
        return 0


def create_order(
    user_id: int,
    quantity: int,
    *,
    niche: str = "",
    city: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """
    Cliente pede N leads. Reserva N Trovoedas.
    Returns { ok, order?, error?, balance? }
    """
    from src.trovoeda import REASON_RESERVE, apply_delta, get_balance

    if not user_id or not _DATABASE_URL:
        return {"ok": False, "error": "usuário inválido"}
    try:
        qty = int(quantity)
    except (TypeError, ValueError):
        return {"ok": False, "error": "quantidade inválida"}
    if qty < 1 or qty > 200:
        return {"ok": False, "error": "quantidade deve ser entre 1 e 200"}

    bal = get_balance(int(user_id))
    if bal < qty:
        return {
            "ok": False,
            "error": f"saldo insuficiente ({bal} Trovoedas; precisa de {qty})",
            "balance": bal,
        }

    # Abaixo de 10 leads: só estoque aleatório (sem filtro de cidade/nicho).
    # Escolha de mercado liberada a partir de 10 (ex.: boas-vindas com 5 moedas).
    FILTER_MIN_QTY = 10
    niche_s = (niche or "").strip()[:120]
    city_s = (city or "").strip()[:120]
    if qty < FILTER_MIN_QTY:
        niche_s = ""
        city_s = ""

    ensure_schema()
    conn = None
    try:
        conn = _connect()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        now = _now()
        cur.execute(
            """
            INSERT INTO lead_orders
                (user_id, quantity, delivered, reserved, niche, city, notes, status, created_at, updated_at)
            VALUES (%s, %s, 0, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *;
            """,
            (
                int(user_id),
                qty,
                qty,
                niche_s,
                city_s,
                (notes or "").strip()[:500],
                STATUS_PENDING,
                now,
                now,
            ),
        )
        order = _row_to_dict(cur.fetchone())
        conn.commit()
        cur.close()
        conn.close()
        conn = None

        # reserva fora da tx do pedido (ledger tem tx própria)
        spent = apply_delta(
            int(user_id),
            -qty,
            reason=REASON_RESERVE,
            note=f"Pedido #{order['id']} · {qty} lead(s)",
            ref_type="order",
            ref_id=str(order["id"]),
        )
        if not spent.get("ok"):
            # reverte o pedido se a reserva falhar
            try:
                c2 = _connect()
                c2.cursor().execute("DELETE FROM lead_orders WHERE id = %s;", (order["id"],))
                c2.commit()
                c2.close()
            except Exception:
                pass
            return {
                "ok": False,
                "error": spent.get("error") or "falha ao reservar Trovoedas",
                "balance": spent.get("balance", bal),
            }

        order = get_order(int(order["id"])) or order
        return {
            "ok": True,
            "order": order,
            "balance": spent.get("balance"),
        }
    except Exception as exc:
        logger.warning("[Orders] create_order: %s", exc)
        try:
            if conn:
                conn.rollback()
                conn.close()
        except Exception:
            pass
        return {"ok": False, "error": str(exc)}


def cancel_order(order_id: int, user_id: int) -> dict[str, Any]:
    """Cliente cancela pedido pendente → estorna reserva."""
    order = get_order(int(order_id))
    if not order:
        return {"ok": False, "error": "pedido não encontrado"}
    if int(order.get("user_id") or 0) != int(user_id):
        return {"ok": False, "error": "pedido de outro usuário"}
    if order.get("status") != STATUS_PENDING:
        return {"ok": False, "error": "só pedidos pendentes podem ser cancelados"}
    return _release_and_close(order, STATUS_CANCELLED, host_note="Cancelado pelo cliente", reviewed_by=user_id)


def reject_order(
    order_id: int,
    *,
    host_id: int | None = None,
    host_note: str = "",
) -> dict[str, Any]:
    """Host recusa → estorna Trovoedas reservadas."""
    order = get_order(int(order_id))
    if not order:
        return {"ok": False, "error": "pedido não encontrado"}
    if order.get("status") != STATUS_PENDING:
        return {"ok": False, "error": "pedido não está pendente"}
    return _release_and_close(
        order,
        STATUS_REJECTED,
        host_note=host_note or "Recusado pelo host",
        reviewed_by=host_id,
    )


def _release_and_close(
    order: dict[str, Any],
    new_status: str,
    *,
    host_note: str = "",
    reviewed_by: int | None = None,
) -> dict[str, Any]:
    from src.trovoeda import REASON_RELEASE, apply_delta

    oid = int(order["id"])
    uid = int(order["user_id"])
    reserved = max(0, int(order.get("reserved") or 0))
    delivered = max(0, int(order.get("delivered") or 0))
    # só devolve o que ainda não foi “entregue” (no pending delivered=0)
    to_release = max(0, reserved - delivered)

    bal = None
    if to_release > 0:
        r = apply_delta(
            uid,
            to_release,
            reason=REASON_RELEASE,
            note=f"Estorno pedido #{oid} · {to_release} Trovoeda(s)",
            ref_type="order",
            ref_id=str(oid),
            created_by=reviewed_by,
        )
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error") or "falha no estorno"}
        bal = r.get("balance")

    ensure_schema()
    try:
        conn = _connect()
        cur = conn.cursor()
        now = _now()
        cur.execute(
            """
            UPDATE lead_orders
            SET status = %s,
                reserved = 0,
                host_note = %s,
                reviewed_by = %s,
                reviewed_at = %s,
                updated_at = %s
            WHERE id = %s AND status = %s;
            """,
            (
                new_status,
                (host_note or "")[:500],
                int(reviewed_by) if reviewed_by else None,
                now,
                now,
                oid,
                STATUS_PENDING,
            ),
        )
        if cur.rowcount == 0:
            conn.rollback()
            cur.close()
            conn.close()
            return {"ok": False, "error": "pedido já foi processado"}
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        logger.warning("[Orders] _release_and_close: %s", exc)
        return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "order": get_order(oid),
        "balance": bal,
        "released": to_release,
    }


def approve_order(
    order_id: int,
    *,
    host_id: int | None = None,
    host_note: str = "",
    auto_deliver: bool = True,
) -> dict[str, Any]:
    """
    Host aprova o pedido.
    Se auto_deliver=True, tenta preencher com sobras do pool (sem re-debitar).
    """
    order = get_order(int(order_id))
    if not order:
        return {"ok": False, "error": "pedido não encontrado"}
    if order.get("status") != STATUS_PENDING:
        return {"ok": False, "error": "pedido não está pendente"}

    oid = int(order["id"])
    ensure_schema()
    try:
        conn = _connect()
        cur = conn.cursor()
        now = _now()
        cur.execute(
            """
            UPDATE lead_orders
            SET status = %s,
                host_note = %s,
                reviewed_by = %s,
                reviewed_at = %s,
                updated_at = %s
            WHERE id = %s AND status = %s;
            """,
            (
                STATUS_APPROVED,
                (host_note or "Aprovado")[:500],
                int(host_id) if host_id else None,
                now,
                now,
                oid,
                STATUS_PENDING,
            ),
        )
        if cur.rowcount == 0:
            conn.rollback()
            cur.close()
            conn.close()
            return {"ok": False, "error": "pedido já foi processado"}
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        logger.warning("[Orders] approve_order: %s", exc)
        return {"ok": False, "error": str(exc)}

    delivered_now = 0
    if auto_deliver:
        fr = fulfill_order(oid, host_id=host_id)
        if fr.get("ok"):
            delivered_now = int(fr.get("delivered_now") or 0)
            return {
                "ok": True,
                "order": fr.get("order") or get_order(oid),
                "delivered_now": delivered_now,
                "message": fr.get("message") or "Pedido aprovado",
            }

    return {
        "ok": True,
        "order": get_order(oid),
        "delivered_now": delivered_now,
        "message": "Pedido aprovado. Aguardando entrega de leads.",
    }


def fulfill_order(
    order_id: int,
    *,
    host_id: int | None = None,
    max_leads: int | None = None,
) -> dict[str, Any]:
    """
    Entrega leads livres do pool para o pedido (já pago via reserva).
    Não debita Trovoeda de novo.
    """
    order = get_order(int(order_id))
    if not order:
        return {"ok": False, "error": "pedido não encontrado"}
    st = order.get("status")
    if st not in (STATUS_APPROVED, STATUS_FULFILLED):
        if st == STATUS_PENDING:
            return {"ok": False, "error": "aprove o pedido antes de entregar"}
        return {"ok": False, "error": f"pedido com status {st} não recebe entrega"}

    remaining = max(0, int(order.get("quantity") or 0) - int(order.get("delivered") or 0))
    if remaining <= 0:
        return {
            "ok": True,
            "order": order,
            "delivered_now": 0,
            "message": "Pedido já está completo",
        }

    want = remaining
    if max_leads is not None:
        try:
            want = max(0, min(remaining, int(max_leads)))
        except (TypeError, ValueError):
            want = remaining
    if want <= 0:
        return {"ok": True, "order": order, "delivered_now": 0, "message": "Nada a entregar"}

    uid = int(order["user_id"])
    city = (order.get("city") or "").strip()
    niche = (order.get("niche") or "").strip()
    n = _assign_pool_leads(uid, want, city=city, niche=niche, order_id=int(order_id))
    if n <= 0:
        return {
            "ok": True,
            "order": get_order(int(order_id)),
            "delivered_now": 0,
            "message": "Sem sobras elegíveis no pool agora. Rode o robô no cockpit ou distribua manualmente.",
        }

    new_delivered = int(order.get("delivered") or 0) + n
    qty = int(order.get("quantity") or 0)
    new_status = STATUS_FULFILLED if new_delivered >= qty else STATUS_APPROVED
    # reserved permanece como “pago”; zera residual só conceitualmente no fulfilled
    new_reserved = max(0, int(order.get("reserved") or 0))  # mantém histórico do que foi pago

    ensure_schema()
    try:
        conn = _connect()
        cur = conn.cursor()
        now = _now()
        cur.execute(
            """
            UPDATE lead_orders
            SET delivered = %s,
                status = %s,
                reserved = %s,
                updated_at = %s
            WHERE id = %s;
            """,
            (new_delivered, new_status, new_reserved, now, int(order_id)),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        logger.warning("[Orders] fulfill update: %s", exc)
        return {"ok": False, "error": str(exc), "delivered_now": n}

    order2 = get_order(int(order_id))
    msg = f"Entregues {n} lead(s). Total {new_delivered}/{qty}."
    if new_status == STATUS_FULFILLED:
        msg += " Pedido completo."
    return {
        "ok": True,
        "order": order2,
        "delivered_now": n,
        "message": msg,
    }


def _assign_pool_leads(
    user_id: int,
    quantity: int,
    *,
    city: str = "",
    niche: str = "",
    order_id: int | None = None,
) -> int:
    """
    Atribui até `quantity` leads livres (Raio/sem site + contato) ao user.
    Sem débito de Trovoeda (já reservado no pedido).
    """
    if not user_id or quantity <= 0 or not _DATABASE_URL:
        return 0

    try:
        from src.users import lead_has_client_contact
    except Exception:
        lead_has_client_contact = None  # type: ignore

    conn = None
    try:
        conn = _connect()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # sobras elegíveis
        sql = """
            SELECT id, name, city, niche, phone, website, website_status, lead_class,
                   lead_score, instagram_url, instagram_username, assigned_to
            FROM companies
            WHERE assigned_to IS NULL
              AND (
                lead_class = 'raio'
                OR website_status IN ('sem_site', 'so_social')
                OR website IS NULL
                OR TRIM(COALESCE(website, '')) = ''
              )
        """
        params: list[Any] = []
        if city:
            sql += " AND LOWER(COALESCE(city,'')) LIKE %s"
            params.append(f"%{city.lower()}%")
        if niche:
            sql += " AND (LOWER(COALESCE(niche,'')) LIKE %s OR LOWER(COALESCE(category,'')) LIKE %s)"
            params.append(f"%{niche.lower()}%")
            params.append(f"%{niche.lower()}%")
        sql += " ORDER BY lead_score DESC NULLS LAST, id DESC LIMIT %s"
        # pega folga pra filtrar contato em Python
        params.append(max(quantity * 5, 40))

        try:
            cur.execute(sql, params)
        except Exception:
            # fallback sem category
            conn.rollback()
            sql2 = """
                SELECT id, name, city, niche, phone, website, website_status, lead_class,
                       lead_score, assigned_to
                FROM companies
                WHERE assigned_to IS NULL
                  AND (
                    lead_class = 'raio'
                    OR website_status IN ('sem_site', 'so_social')
                    OR website IS NULL
                    OR TRIM(COALESCE(website, '')) = ''
                  )
                ORDER BY lead_score DESC NULLS LAST, id DESC
                LIMIT %s;
            """
            cur.execute(sql2, (max(quantity * 5, 40),))

        candidates = [dict(r) for r in cur.fetchall()]
        picked: list[int] = []
        for c in candidates:
            if len(picked) >= quantity:
                break
            # bloqueia gigantes se o helper existir
            try:
                from src.contact import has_usable_contact, is_giant_enterprise
                if is_giant_enterprise(c):
                    continue
                if not has_usable_contact(c):
                    continue
            except Exception:
                if lead_has_client_contact and not lead_has_client_contact(c):
                    continue
            picked.append(int(c["id"]))

        if not picked:
            cur.close()
            conn.close()
            return 0

        now = _now()
        assigned = 0
        for lid in picked:
            cur.execute(
                """
                UPDATE companies
                SET assigned_to = %s, assigned_at = %s
                WHERE id = %s AND assigned_to IS NULL;
                """,
                (int(user_id), now, lid),
            )
            if cur.rowcount:
                assigned += 1

        conn.commit()
        cur.close()
        conn.close()
        logger.info(
            "[Orders] assigned %s lead(s) user=%s order=%s",
            assigned,
            user_id,
            order_id,
        )
        return assigned
    except Exception as exc:
        logger.warning("[Orders] _assign_pool_leads: %s", exc)
        try:
            if conn:
                conn.rollback()
                conn.close()
        except Exception:
            pass
        return 0
