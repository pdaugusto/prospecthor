"""
audit.py — Log de auditoria do ProspectHOR
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("audit")

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
            CREATE TABLE IF NOT EXISTS audit_log (
                id              SERIAL PRIMARY KEY,
                created_at      TEXT NOT NULL,
                user_id         INTEGER,
                username        TEXT,
                action          TEXT NOT NULL,
                lead_id         INTEGER,
                company_name    TEXT,
                details         TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log (created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log (username);
            CREATE INDEX IF NOT EXISTS idx_audit_lead ON audit_log (lead_id);
            """
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


def log_action(
    action: str,
    *,
    user_id: int | None = None,
    username: str | None = None,
    lead_id: int | None = None,
    company_name: str | None = None,
    details: str | dict | None = None,
) -> None:
    """Registra uma ação. Falha silenciosa para não quebrar o fluxo principal."""
    if not _DATABASE_URL or not action:
        return
    try:
        ensure_schema()
        if isinstance(details, dict):
            details_s = json.dumps(details, ensure_ascii=False)
        else:
            details_s = str(details or "")
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO audit_log
                (created_at, user_id, username, action, lead_id, company_name, details)
            VALUES (%s, %s, %s, %s, %s, %s, %s);
            """,
            (
                datetime.now().isoformat(),
                user_id,
                username or "sistema",
                action,
                lead_id,
                company_name or "",
                details_s,
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        logger.warning("audit log falhou: %s", exc)


def query_logs(
    *,
    username: str | None = None,
    lead_id: int | None = None,
    action: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    ensure_schema()
    sql = "SELECT * FROM audit_log WHERE 1=1"
    params: list[Any] = []
    if username:
        sql += " AND username ILIKE %s"
        params.append(f"%{username}%")
    if lead_id:
        sql += " AND lead_id = %s"
        params.append(int(lead_id))
    if action:
        sql += " AND action = %s"
        params.append(action)
    if since:
        sql += " AND created_at >= %s"
        params.append(since)
    if until:
        sql += " AND created_at <= %s"
        params.append(until)
    sql += " ORDER BY id DESC LIMIT %s"
    params.append(max(1, min(int(limit or 200), 500)))

    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows
    finally:
        conn.close()
