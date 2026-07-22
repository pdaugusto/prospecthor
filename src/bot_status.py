"""
bot_status.py — Status do robô de prospecção (para o dashboard)

O bot local grava aqui; o painel só lê.

Meta compartilhada Maps + Fonte B:
  - session_leads_count / mission_target  → total da missão (único teto)
  - niche_counts (JSON)                  → só stats (NÃO limita por nicho)
  - niche_quotas                         → desligado (sempre vazio)
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
logger = logging.getLogger("bot_status")

_DATABASE_URL = os.getenv("DATABASE_URL", "")


def _connect():
    if not _DATABASE_URL:
        raise RuntimeError("DATABASE_URL não configurada")
    return psycopg2.connect(_DATABASE_URL)


def _parse_json_dict(raw: Any) -> dict[str, int]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        out: dict[str, int] = {}
        for k, v in raw.items():
            if k is None:
                continue
            try:
                out[str(k)] = int(v or 0)
            except (TypeError, ValueError):
                out[str(k)] = 0
        return out
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            return _parse_json_dict(json.loads(s))
        except Exception:
            return {}
    return {}


def _json_dumps_counts(d: dict[str, int] | None) -> str:
    clean: dict[str, int] = {}
    for k, v in (d or {}).items():
        if not k:
            continue
        try:
            clean[str(k)] = max(0, int(v or 0))
        except (TypeError, ValueError):
            clean[str(k)] = 0
    return json.dumps(clean, ensure_ascii=False)


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
        # meta compartilhada Maps + Fonte B
        for col, ddl in (
            ("mission_target", "ALTER TABLE bot_runtime ADD COLUMN mission_target INTEGER DEFAULT 0;"),
            ("niche_quotas", "ALTER TABLE bot_runtime ADD COLUMN niche_quotas TEXT DEFAULT '{}';"),
            ("niche_counts", "ALTER TABLE bot_runtime ADD COLUMN niche_counts TEXT DEFAULT '{}';"),
        ):
            cur.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'bot_runtime' AND column_name = %s;
                """,
                (col,),
            )
            if not cur.fetchone():
                try:
                    cur.execute(ddl)
                except Exception:
                    pass
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
            # session_leads=None → não zera o contador (ex.: Fonte B em paralelo ao Maps)
            if session_leads is not None:
                cur.execute(
                    """
                    UPDATE bot_runtime SET
                        status = %s,
                        last_started_at = %s,
                        last_error = NULL,
                        session_leads_count = %s,
                        last_job = COALESCE(%s, last_job),
                        updated_at = %s
                    WHERE id = 1;
                    """,
                    (status, now, int(session_leads), last_job, now),
                )
            else:
                cur.execute(
                    """
                    UPDATE bot_runtime SET
                        status = %s,
                        last_error = NULL,
                        last_job = COALESCE(%s, last_job),
                        updated_at = %s
                    WHERE id = 1;
                    """,
                    (status, last_job, now),
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


def get_session_leads() -> int:
    """Contagem compartilhada da missão (Maps + Fonte B)."""
    if not _DATABASE_URL:
        return 0
    try:
        ensure_schema()
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(session_leads_count, 0) FROM bot_runtime WHERE id = 1;")
        row = cur.fetchone()
        cur.close()
        conn.close()
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0


def get_mission_target() -> int:
    """Meta da missão atual (0 = sem teto)."""
    if not _DATABASE_URL:
        return 0
    try:
        ensure_schema()
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(mission_target, 0) FROM bot_runtime WHERE id = 1;")
        row = cur.fetchone()
        cur.close()
        conn.close()
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0


def get_niche_quotas() -> dict[str, int]:
    """Cotas por nicho da missão (divididas entre Maps + Fonte B)."""
    if not _DATABASE_URL:
        return {}
    try:
        ensure_schema()
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT niche_quotas FROM bot_runtime WHERE id = 1;")
        row = cur.fetchone()
        cur.close()
        conn.close()
        return _parse_json_dict(row[0] if row else None)
    except Exception:
        return {}


def get_niche_counts() -> dict[str, int]:
    """Contagem por nicho compartilhada (Maps + Fonte B)."""
    if not _DATABASE_URL:
        return {}
    try:
        ensure_schema()
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT niche_counts FROM bot_runtime WHERE id = 1;")
        row = cur.fetchone()
        cur.close()
        conn.close()
        return _parse_json_dict(row[0] if row else None)
    except Exception:
        return {}


def get_niche_count(niche: str | None) -> int:
    if not niche:
        return 0
    return int(get_niche_counts().get(str(niche), 0) or 0)


def is_niche_full(niche: str | None) -> bool:
    """Desativado: sem cota por nicho — sempre False (pode encher de qualquer nicho)."""
    return False


def remaining_to_niche(niche: str | None) -> int | None:
    """Desativado: sem teto por nicho (None = ilimitado no nicho)."""
    return None


def any_niche_has_room() -> bool:
    """Sem cotas por nicho — sempre há 'vaga' no sentido de nicho."""
    return True


def all_niche_quotas_met() -> bool:
    """Desativado: não usamos mais cotas por nicho para parar a missão."""
    return False


def set_mission_meta(
    target: int,
    *,
    reset_leads: bool = False,
    niche_quotas: dict[str, int] | None = None,
) -> None:
    """
    Define a meta compartilhada Maps ↔ Fonte B.
    reset_leads=True zera contador total + contagens por nicho.
    niche_quotas: divisão da meta (ex. {"restaurante": 5, "salao": 5}).
      - se passado (mesmo {}), grava no banco
      - se None e reset_leads, zera as cotas
      - se None e não reset, mantém cotas atuais
    """
    if not _DATABASE_URL:
        return
    try:
        ensure_schema()
        conn = _connect()
        cur = conn.cursor()
        now = datetime.now().isoformat()
        tgt = max(0, int(target or 0))
        # Cotas por nicho DESLIGADAS (sempre {}): meta total livre entre nichos.
        # Aceita niche_quotas no signature por compat, mas não aplica divisão.
        if reset_leads:
            job = f"meta_start 0/{tgt}" if tgt else "meta_start"
            cur.execute(
                """
                UPDATE bot_runtime SET
                    mission_target = %s,
                    session_leads_count = 0,
                    niche_quotas = %s,
                    niche_counts = %s,
                    last_job = %s,
                    updated_at = %s
                WHERE id = 1;
                """,
                (tgt, "{}", "{}", job, now),
            )
        else:
            cur.execute(
                """
                UPDATE bot_runtime SET
                    mission_target = %s,
                    niche_quotas = %s,
                    updated_at = %s
                WHERE id = 1;
                """,
                (tgt, "{}", now),
            )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        logger.warning("bot_status set_mission_meta: %s", exc)


def remaining_to_meta() -> int | None:
    """
    Quantos leads ainda faltam para a meta compartilhada.
    None = sem meta (ilimitado).
    Ex.: meta 20 e got 19 → retorna 1 (ainda pode gravar o 20º).
    """
    tgt = get_mission_target()
    if tgt <= 0:
        return None
    got = get_session_leads()
    return max(0, int(tgt) - int(got))


def meta_reached() -> bool:
    """
    True só quando contagem >= meta (ex.: 20/20).
    19/20 → False (ainda falta o último).
    """
    tgt = get_mission_target()
    if tgt <= 0:
        return False
    return get_session_leads() >= tgt


def try_claim_one_lead(niche: str | None = None) -> tuple[bool, int, int]:
    """
    Reserva 1 vaga na meta TOTAL compartilhada (Maps ↔ Fonte B) de forma atômica.

    SEM cota por nicho: pode encher de um nicho só se quiser (mais rápido).
    O parâmetro `niche` só atualiza contagem informativa (stats), não bloqueia.

    Returns:
        (ok, total_atual, meta)
        ok=False → meta total cheia
    Quando o claim enche a meta, grava last_job='META_OK' para os 2 workers pararem.
    """
    if not _DATABASE_URL:
        return True, 0, 0
    conn = None
    try:
        ensure_schema()
        conn = _connect()
        cur = conn.cursor()
        cur.execute("BEGIN")
        cur.execute(
            """
            SELECT COALESCE(session_leads_count, 0),
                   COALESCE(mission_target, 0),
                   niche_counts
            FROM bot_runtime WHERE id = 1 FOR UPDATE;
            """
        )
        row = cur.fetchone() or (0, 0, "{}")
        cur_count = int(row[0] or 0)
        tgt = int(row[1] or 0)
        counts = _parse_json_dict(row[2])
        now = datetime.now().isoformat()
        nid = (str(niche).strip() if niche else "") or ""

        if tgt > 0 and cur_count >= tgt:
            cur.execute(
                """
                UPDATE bot_runtime SET
                    last_job = %s,
                    updated_at = %s
                WHERE id = 1;
                """,
                (f"META_OK {cur_count}/{tgt}", now),
            )
            conn.commit()
            cur.close()
            conn.close()
            return False, cur_count, tgt

        new_count = cur_count + 1
        if nid:
            counts[nid] = int(counts.get(nid, 0) or 0) + 1
        counts_json = _json_dumps_counts(counts)
        last_job = None
        if tgt > 0 and new_count >= tgt:
            last_job = f"META_OK {new_count}/{tgt}"
        elif nid:
            last_job = f"meta {new_count}/{tgt or '∞'} | {nid}={counts.get(nid, 0)}"

        if last_job:
            cur.execute(
                """
                UPDATE bot_runtime SET
                    session_leads_count = %s,
                    niche_counts = %s,
                    last_job = %s,
                    updated_at = %s
                WHERE id = 1;
                """,
                (new_count, counts_json, last_job, now),
            )
        else:
            cur.execute(
                """
                UPDATE bot_runtime SET
                    session_leads_count = %s,
                    niche_counts = %s,
                    updated_at = %s
                WHERE id = 1;
                """,
                (new_count, counts_json, now),
            )
        conn.commit()
        cur.close()
        conn.close()
        return True, new_count, tgt
    except Exception as exc:
        logger.warning("bot_status try_claim_one_lead: %s", exc)
        try:
            if conn is not None:
                conn.rollback()
                conn.close()
        except Exception:
            pass
        # fallback: se o banco falhar, deixa gravar (não trava o bot)
        return True, get_session_leads(), get_mission_target()


def should_stop_for_meta() -> bool:
    """
    True só se contagem real >= meta (20/20).
    19/20 → False. Não usa last_job META_OK sozinho (parava cedo).
    """
    return meta_reached()


def release_one_lead(niche: str | None = None) -> None:
    """Devolve 1 vaga se o save falhou depois do claim (total + nicho)."""
    if not _DATABASE_URL:
        return
    try:
        ensure_schema()
        conn = _connect()
        cur = conn.cursor()
        cur.execute("BEGIN")
        cur.execute(
            """
            SELECT COALESCE(session_leads_count, 0), niche_counts
            FROM bot_runtime WHERE id = 1 FOR UPDATE;
            """
        )
        row = cur.fetchone() or (0, "{}")
        cur_count = max(0, int(row[0] or 0) - 1)
        counts = _parse_json_dict(row[1])
        nid = (str(niche).strip() if niche else "") or ""
        if nid and nid in counts:
            counts[nid] = max(0, int(counts.get(nid, 0) or 0) - 1)
        elif nid:
            # se não estava no dict, não inventa negativo
            pass
        now = datetime.now().isoformat()
        cur.execute(
            """
            UPDATE bot_runtime SET
                session_leads_count = %s,
                niche_counts = %s,
                last_job = CASE
                    WHEN last_job LIKE 'META_OK%%' THEN 'meta_open'
                    ELSE last_job
                END,
                updated_at = %s
            WHERE id = 1;
            """,
            (cur_count, _json_dumps_counts(counts), now),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        logger.warning("bot_status release_one_lead: %s", exc)


def increment_session_leads(n: int = 1, niche: str | None = None) -> int:
    """
    Soma leads na meta compartilhada. Retorna o total após o incremento.
    Não passa da meta (teto): se faltam 2 e n=5, só soma 2.
    Prefira try_claim_one_lead() por lead para Maps e Fonte B se entenderem em tempo real.
    """
    if not _DATABASE_URL or n <= 0:
        return get_session_leads()
    total = get_session_leads()
    for _ in range(int(n)):
        ok, total, _tgt = try_claim_one_lead(niche=niche)
        if not ok:
            break
    return total


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
        # expõe cotas/contagens por nicho como dict (Maps + Fonte B)
        data["niche_quotas"] = _parse_json_dict(data.get("niche_quotas"))
        data["niche_counts"] = _parse_json_dict(data.get("niche_counts"))

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
