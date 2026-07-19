"""
bot_status.py — Status do robô de prospecção (para o dashboard)

O bot local grava aqui; o painel só lê.
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
logger = logging.getLogger("bot_status")

_DATABASE_URL = os.getenv("DATABASE_URL", "")


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
            CREATE TABLE IF NOT EXISTS bot_runtime (
                id                  INTEGER PRIMARY KEY DEFAULT 1,
                status              TEXT NOT NULL DEFAULT 'parado',
                last_started_at     TEXT,
                last_finished_at    TEXT,
                last_error          TEXT,
                last_leads_count    INTEGER DEFAULT 0,
                session_leads_count INTEGER DEFAULT 0,
                last_job            TEXT,
                updated_at          TEXT
            );
            """
        )
        cur.execute(
            """
            INSERT INTO bot_runtime (id, status, updated_at)
            VALUES (1, 'parado', %s)
            ON CONFLICT (id) DO NOTHING;
            """,
            (datetime.now().isoformat(),),
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_log_lines (
                id          SERIAL PRIMARY KEY,
                created_at  TEXT NOT NULL,
                level       TEXT DEFAULT 'INFO',
                message     TEXT NOT NULL
            );
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_bot_log_created ON bot_log_lines (id DESC);"
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


def set_status(
    status: str,
    *,
    last_job: str | None = None,
    last_error: str | None = None,
    session_leads: int | None = None,
    last_leads: int | None = None,
) -> None:
    if not _DATABASE_URL:
        return
    try:
        ensure_schema()
        now = datetime.now().isoformat()
        conn = _connect()
        cur = conn.cursor()
        if status == "rodando":
            cur.execute(
                """
                UPDATE bot_runtime SET
                    status = %s,
                    last_started_at = %s,
                    last_error = NULL,
                    session_leads_count = COALESCE(%s, 0),
                    last_job = COALESCE(%s, last_job),
                    updated_at = %s
                WHERE id = 1;
                """,
                (status, now, session_leads if session_leads is not None else 0, last_job, now),
            )
        elif status == "parado":
            cur.execute(
                """
                UPDATE bot_runtime SET
                    status = %s,
                    last_finished_at = %s,
                    last_leads_count = COALESCE(%s, last_leads_count),
                    session_leads_count = COALESCE(%s, session_leads_count),
                    last_job = COALESCE(%s, last_job),
                    last_error = NULL,
                    updated_at = %s
                WHERE id = 1;
                """,
                (status, now, last_leads, session_leads, last_job, now),
            )
        elif status == "erro":
            cur.execute(
                """
                UPDATE bot_runtime SET
                    status = %s,
                    last_finished_at = %s,
                    last_error = %s,
                    last_job = COALESCE(%s, last_job),
                    updated_at = %s
                WHERE id = 1;
                """,
                (status, now, (last_error or "")[:500], last_job, now),
            )
        else:
            cur.execute(
                """
                UPDATE bot_runtime SET status = %s, updated_at = %s,
                    last_job = COALESCE(%s, last_job)
                WHERE id = 1;
                """,
                (status, now, last_job),
            )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        logger.warning("bot_status set_status: %s", exc)


def add_log(message: str, level: str = "INFO") -> None:
    if not _DATABASE_URL or not message:
        return
    try:
        ensure_schema()
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO bot_log_lines (created_at, level, message)
            VALUES (%s, %s, %s);
            """,
            (datetime.now().isoformat(), level, str(message)[:1000]),
        )
        # mantém só as últimas ~200 linhas
        cur.execute(
            """
            DELETE FROM bot_log_lines
            WHERE id < (
                SELECT COALESCE(MAX(id), 0) - 200 FROM bot_log_lines
            );
            """
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        logger.warning("bot_status add_log: %s", exc)


def increment_session_leads(n: int = 1) -> None:
    if not _DATABASE_URL or n <= 0:
        return
    try:
        ensure_schema()
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE bot_runtime SET
                session_leads_count = COALESCE(session_leads_count, 0) + %s,
                updated_at = %s
            WHERE id = 1;
            """,
            (int(n), datetime.now().isoformat()),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        logger.warning("bot_status increment: %s", exc)


# Se "rodando" sem atualização por mais que isso, considera morto (Ctrl+C, fechou CMD, crash)
_STALE_RODANDO_MINUTES = int(os.getenv("BOT_STALE_MINUTES") or "20")


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "").split("+")[0])
    except Exception:
        return None


def force_parado(
    *,
    reason: str = "marcado parado manualmente",
    last_job: str | None = None,
) -> dict[str, Any]:
    """Força status parado (dashboard ou auto-stale)."""
    set_status(
        "parado",
        last_job=last_job or reason,
        last_leads=None,
        session_leads=None,
    )
    add_log(reason, level="WARN")
    return get_status(15)


def get_status(log_limit: int = 15) -> dict[str, Any]:
    if not _DATABASE_URL:
        return {"status": "desconhecido", "logs": [], "stale": False}
    try:
        ensure_schema()
        conn = _connect()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM bot_runtime WHERE id = 1;")
        row = cur.fetchone()
        cur.execute(
            """
            SELECT created_at, level, message
            FROM bot_log_lines
            ORDER BY id DESC
            LIMIT %s;
            """,
            (max(1, min(int(log_limit or 15), 50)),),
        )
        logs = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        data = dict(row) if row else {"status": "parado"}
        data["logs"] = logs
        data["stale"] = False

        # Auto-corrige "rodando" fantasma (bot morto sem gravar parado)
        st = (data.get("status") or "").lower().strip()
        if st == "rodando":
            updated = _parse_iso(data.get("updated_at")) or _parse_iso(data.get("last_started_at"))
            age_min = None
            if updated is not None:
                age_min = (datetime.now() - updated).total_seconds() / 60.0
                data["minutes_since_update"] = round(age_min, 1)
            stale = updated is None or (age_min is not None and age_min > _STALE_RODANDO_MINUTES)
            if stale:
                data["stale"] = True
                if updated is None:
                    msg = "auto: rodando sem updated_at → parado"
                else:
                    msg = (
                        f"auto: sem heartbeat há {int(age_min)} min "
                        f"(limite {_STALE_RODANDO_MINUTES}) → parado"
                    )
                try:
                    set_status(
                        "parado",
                        last_job=msg,
                        last_leads=data.get("session_leads_count"),
                        session_leads=data.get("session_leads_count"),
                    )
                    add_log(msg, level="WARN")
                except Exception:
                    pass
                # devolve já corrigido (sem recursão)
                data["status"] = "parado"
                data["last_job"] = msg
                data["last_finished_at"] = datetime.now().isoformat()

        return data
    except Exception as exc:
        logger.warning("bot_status get: %s", exc)
        return {"status": "erro", "last_error": str(exc), "logs": [], "stale": False}
