"""
db.py — Pool de conexões PostgreSQL reutilizáveis.

O pooler do Supabase cobra ~0.9s só para ABRIR cada conexão nova. Os fluxos
de missão abrem 1 conexão por operação (upsert, score, claim, contagem...) —
isso adiciona ~1s por lead. Este módulo mantém conexões quentes e as reutiliza.

Pool LIFO próprio (o ThreadedConnectionPool do psycopg2 com minconn=0 NÃO
reusa conexão até encher o maxconn — cria sempre nova; por isso o pool manual).

Env:
    DB_POOL      = "0" desliga o pool (volta a abrir conexão nova)
    DB_POOL_MAX  = máx. de conexões quentes mantidas (padrão 8)
"""

from __future__ import annotations

import os
import threading

import psycopg2
import psycopg2.extensions as _ext

_DATABASE_URL = os.getenv("DATABASE_URL", "")

_FREE: list = []
_LOCK = threading.Lock()
_POOL_MAX = int(os.getenv("DB_POOL_MAX", "8") or "8")
_POOL_ENABLED = (os.getenv("DB_POOL", "1") or "1").strip().lower() not in (
    "0", "false", "no", "off",
)


class _PooledConnection:
    """Proxy sobre psycopg2; close() devolve a conexão ao pool."""

    __slots__ = ("_conn", "_closed")

    def __init__(self, conn) -> None:
        self._conn = conn
        self._closed = False

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def cursor(self, *a, **kw):
        return self._conn.cursor(*a, **kw)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._conn.closed == 0:
            try:
                st = self._conn.get_transaction_status()
                if st == _ext.TRANSACTION_STATUS_UNKNOWN:
                    # conexão morta (servidor fechou) — não volta pro pool
                    self._conn.close()
                    return
                if st != _ext.TRANSACTION_STATUS_IDLE:
                    self._conn.rollback()
            except Exception:
                try:
                    self._conn.close()
                except Exception:
                    pass
                return
        with _LOCK:
            if _POOL_ENABLED and len(_FREE) < _POOL_MAX:
                _FREE.append(self._conn)
            else:
                try:
                    self._conn.close()
                except Exception:
                    pass


def _conn_ok(conn) -> bool:
    """Probe barato (~1ms na conexão quente): se o servidor matou a conexão, descarta."""
    try:
        if conn.closed != 0:
            return False
        st = conn.get_transaction_status()
        if st == _ext.TRANSACTION_STATUS_UNKNOWN:
            return False
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.rollback()
        return True
    except Exception:
        return False


def connect():
    """
    Conexão reutilizável (devolvida no .close()) ou nova se o pool estiver vazio.

    Conexão morta (o pooler do Supabase fecha conexões ociosas) é detectada no
    checkout e descartada — nunca volta pro pool nem quebra a operação seguinte.
    """
    if not _DATABASE_URL:
        raise RuntimeError("DATABASE_URL não configurada")
    while _POOL_ENABLED:
        with _LOCK:
            if not _FREE:
                break
            pooled = _FREE.pop()
        if _conn_ok(pooled):
            return _PooledConnection(pooled)
        try:
            pooled.close()
        except Exception:
            pass
        # descarta e tenta outra conexão quente (ou cria nova)
    return _PooledConnection(psycopg2.connect(_DATABASE_URL))


def close_all() -> None:
    """Fecha todas as conexões quentes (usar em atexit/quitar processo)."""
    with _LOCK:
        while _FREE:
            try:
                _FREE.pop().close()
            except Exception:
                pass