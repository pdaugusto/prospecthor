"""
bot_plan.py — Plano de busca do robô (editável pelo dashboard)

O Patrão escolhe no painel:
  - meta de leads da sessão (ex: 20 pro Fafa)
  - cidades
  - nichos

Salvo no Postgres. O bot local lê ao iniciar `python main.py run`.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("bot_plan")

_DATABASE_URL = os.getenv("DATABASE_URL", "")
_BASE = Path(__file__).resolve().parent.parent
_CITIES_PATH = _BASE / "config" / "cities.json"
_NICHES_PATH = _BASE / "config" / "niches.json"


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
            CREATE TABLE IF NOT EXISTS bot_run_plan (
                id              INTEGER PRIMARY KEY DEFAULT 1,
                target_leads    INTEGER NOT NULL DEFAULT 20,
                city_ids        TEXT NOT NULL DEFAULT '[]',
                niche_ids       TEXT NOT NULL DEFAULT '[]',
                notes           TEXT DEFAULT '',
                updated_at      TEXT,
                updated_by      TEXT
            );
            """
        )
        cur.execute(
            """
            INSERT INTO bot_run_plan (id, target_leads, city_ids, niche_ids, updated_at)
            VALUES (1, 20, '[]', '[]', %s)
            ON CONFLICT (id) DO NOTHING;
            """,
            (datetime.now().isoformat(),),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


def _parse_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw if x is not None and str(x).strip()]
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x) for x in data if x is not None and str(x).strip()]
    except Exception:
        pass
    return []


def load_catalog() -> dict[str, Any]:
    """Catálogo de cidades e nichos a partir dos JSON do projeto."""
    cities: list[dict[str, Any]] = []
    niches: list[dict[str, Any]] = []
    try:
        if _CITIES_PATH.exists():
            with open(_CITIES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            for c in data.get("cidades") or data.get("cities") or []:
                cities.append({
                    "id": c.get("id") or f"{c.get('nome','')}_{c.get('estado','')}".lower(),
                    "nome": c.get("nome") or c.get("name") or "",
                    "estado": c.get("estado") or c.get("state") or "",
                    "ativo_json": bool(c.get("ativo", True)),
                    "priority": c.get("priority") or "",
                    "bairros_count": len(c.get("bairros") or c.get("areas") or []),
                })
    except Exception as exc:
        logger.warning("[bot_plan] cities.json: %s", exc)

    try:
        if _NICHES_PATH.exists():
            with open(_NICHES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            for n in data.get("nichos") or data.get("niches") or []:
                niches.append({
                    "id": n.get("id") or "",
                    "label": n.get("label") or n.get("id") or "",
                    "query_term": n.get("query_term") or "",
                })
    except Exception as exc:
        logger.warning("[bot_plan] niches.json: %s", exc)

    return {"cities": cities, "niches": niches}


def get_plan() -> dict[str, Any]:
    """Plano atual + catálogo (para o painel)."""
    ensure_schema()
    catalog = load_catalog()
    default = {
        "target_leads": 20,
        "city_ids": [],
        "niche_ids": [n["id"] for n in catalog.get("niches") or []],
        "notes": "",
        "updated_at": None,
        "updated_by": None,
        "catalog": catalog,
    }
    if not _DATABASE_URL:
        return default
    try:
        conn = _connect()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT target_leads, city_ids, niche_ids, notes, updated_at, updated_by
            FROM bot_run_plan WHERE id = 1 LIMIT 1;
            """
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return default
        city_ids = _parse_list(row.get("city_ids"))
        niche_ids = _parse_list(row.get("niche_ids"))
        # se nichos vazios no plano, default = todos do catálogo
        if not niche_ids:
            niche_ids = [n["id"] for n in catalog.get("niches") or [] if n.get("id")]
        return {
            "target_leads": int(row.get("target_leads") or 20),
            "city_ids": city_ids,
            "niche_ids": niche_ids,
            "notes": row.get("notes") or "",
            "updated_at": row.get("updated_at"),
            "updated_by": row.get("updated_by"),
            "catalog": catalog,
        }
    except Exception as exc:
        logger.warning("[bot_plan] get_plan: %s", exc)
        return default


def save_plan(
    *,
    target_leads: int = 20,
    city_ids: list[str] | None = None,
    niche_ids: list[str] | None = None,
    notes: str = "",
    updated_by: str | None = None,
) -> dict[str, Any]:
    ensure_schema()
    target = max(0, int(target_leads or 0))
    cities = [str(x).strip() for x in (city_ids or []) if str(x).strip()]
    niches = [str(x).strip() for x in (niche_ids or []) if str(x).strip()]
    now = datetime.now().isoformat()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO bot_run_plan (id, target_leads, city_ids, niche_ids, notes, updated_at, updated_by)
            VALUES (1, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                target_leads = EXCLUDED.target_leads,
                city_ids = EXCLUDED.city_ids,
                niche_ids = EXCLUDED.niche_ids,
                notes = EXCLUDED.notes,
                updated_at = EXCLUDED.updated_at,
                updated_by = EXCLUDED.updated_by;
            """,
            (
                target,
                json.dumps(cities, ensure_ascii=False),
                json.dumps(niches, ensure_ascii=False),
                (notes or "")[:500],
                now,
                (updated_by or "")[:80],
            ),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()
    logger.warning(
        "[bot_plan] salvo: target=%s cities=%s niches=%s by=%s",
        target,
        len(cities),
        niches,
        updated_by,
    )
    return get_plan()


def apply_plan_to_job_sources(
    cities_data: list[dict],
    niches_data: list[dict],
    plan: dict[str, Any] | None = None,
) -> tuple[list[dict], list[dict], int]:
    """
    Filtra cities/niches do JSON conforme o plano do painel.

    - city_ids vazio → usa cidades com ativo=true no JSON (comportamento antigo)
    - city_ids com itens → só essas (marca ativo=True nelas)
    - niche_ids vazio → todos os nichos do JSON
    - retorna também target_leads (0 = sem limite de leads na sessão)
    """
    plan = plan if plan is not None else get_plan()
    target = int(plan.get("target_leads") or 0)
    city_ids = set(plan.get("city_ids") or [])
    niche_ids = set(plan.get("niche_ids") or [])

    cities_out: list[dict] = []
    if city_ids:
        for c in cities_data:
            cid = c.get("id") or ""
            nome = (c.get("nome") or "").strip()
            estado = (c.get("estado") or "").strip()
            alt = f"{nome}_{estado}".lower().replace(" ", "_")
            if cid in city_ids or alt in city_ids or nome in city_ids:
                cc = dict(c)
                cc["ativo"] = True
                cities_out.append(cc)
        if not cities_out:
            logger.warning(
                "[bot_plan] Nenhuma cidade do plano bateu com cities.json (%s ids). Usando ativos do JSON.",
                len(city_ids),
            )
            cities_out = [c for c in cities_data if c.get("ativo")]
    else:
        cities_out = [c for c in cities_data if c.get("ativo")]

    if niche_ids:
        niches_out = [n for n in niches_data if (n.get("id") or "") in niche_ids]
        if not niches_out:
            logger.warning("[bot_plan] Nichos do plano não encontrados; usando todos.")
            niches_out = list(niches_data)
    else:
        niches_out = list(niches_data)

    logger.warning(
        "[bot_plan] fila: %s cidade(s), %s nicho(s), meta_leads=%s",
        len(cities_out),
        len(niches_out),
        target or "∞",
    )
    return cities_out, niches_out, target
