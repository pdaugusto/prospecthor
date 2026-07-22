"""
coverage.py — Cobertura de busca por ÁREA (bairro/cidade), não só empresa.

Evita rebuscar o mesmo bairro em SP/RJ. Persistido em data/search_coverage.json.
Unidade: niche | city | state | area
  area = nome do bairro ou "_cidade" (busca genérica da cidade)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

_BASE = Path(__file__).resolve().parent.parent
_PATH = _BASE / "data" / "search_coverage.json"


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _key(niche: str, city: str, state: str, area: str) -> str:
    return "|".join(
        [
            _norm(niche),
            _norm(city),
            (state or "").strip().upper(),
            _norm(area) or "_cidade",
        ]
    )


def load() -> dict[str, Any]:
    if not _PATH.exists():
        return {"done": {}, "updated_at": None}
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("done", {})
        return data
    except Exception as exc:
        logger.warning(f"[Coverage] Falha ao ler: {exc}")
        return {"done": {}, "updated_at": None}


def save(data: dict[str, Any]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now().isoformat()
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_done(niche: str, city: str, state: str, area: str) -> bool:
    data = load()
    return _key(niche, city, state, area) in data.get("done", {})


def mark_done(
    niche: str,
    city: str,
    state: str,
    area: str,
    leads_found: int = 0,
    note: str = "",
) -> None:
    data = load()
    data.setdefault("done", {})[_key(niche, city, state, area)] = {
        "niche": niche,
        "city": city,
        "state": state,
        "area": area or "_cidade",
        "leads_found": leads_found,
        "completed_at": datetime.now().isoformat(),
        "note": note,
    }
    save(data)
    logger.info(
        f"[Coverage] ✓ Área concluída: {niche} | {city}-{state} | {area or '_cidade'} "
        f"({leads_found} leads)"
    )


def seed_from_cities_config(cities: list[dict[str, Any]], niches: list[dict[str, Any]]) -> int:
    """
    Marca ja_varridos do cities.json como feitos (sem sobrescrever timestamps existentes).
    """
    data = load()
    added = 0
    niche_ids = [n.get("id") for n in niches if n.get("id")]
    for city in cities:
        nome = city.get("nome") or ""
        estado = city.get("estado") or ""
        ja = city.get("ja_varridos") or {}
        # ja_varridos: { "odontologia": ["centro", "zona sul"], "advocacia": ["_cidade"] }
        # ou lista global aplicada a todos nichos
        if isinstance(ja, list):
            ja = {nid: ja for nid in niche_ids}
        if not isinstance(ja, dict):
            continue
        for niche_id, areas in ja.items():
            if niche_id not in niche_ids and niche_ids:
                # se key é "*" aplica a todos
                if niche_id != "*":
                    continue
            targets = niche_ids if niche_id == "*" else [niche_id]
            for nid in targets:
                for area in areas or []:
                    k = _key(nid, nome, estado, area)
                    if k not in data["done"]:
                        data["done"][k] = {
                            "niche": nid,
                            "city": nome,
                            "state": estado,
                            "area": area,
                            "leads_found": 0,
                            "completed_at": datetime.now().isoformat(),
                            "seeded": True,
                            "note": "seed cities.json ja_varridos",
                        }
                        added += 1
    if added:
        save(data)
        logger.info(f"[Coverage] Seed: {added} áreas marcadas como já varridas.")
    return added


def list_pending_jobs(
    cities: list[dict[str, Any]],
    niches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Expande cidade × nicho × bairro em jobs pendentes.
    Ordena: prioridade da cidade, depois áreas virgens.
    """
    seed_from_cities_config(cities, niches)
    prio_rank = {"alta": 0, "media": 1, "baixa": 2, "pausada": 9}
    jobs: list[dict[str, Any]] = []

    for city in cities:
        if not city.get("ativo"):
            continue
        c_name = city["nome"]
        c_state = city["estado"]
        bairros = list(city.get("bairros") or [])
        # Se tem bairros, cada bairro é um job. Busca genérica "_cidade" só se allow_citywide
        allow_citywide = city.get("allow_citywide", len(bairros) == 0)
        areas: list[str] = list(bairros)
        if allow_citywide or not bairros:
            if "_cidade" not in areas:
                areas.append("_cidade")

        for niche in niches:
            n_id = niche["id"]
            q_term = niche.get("query_term") or n_id
            for area in areas:
                if is_done(n_id, c_name, c_state, area):
                    continue
                jobs.append(
                    {
                        "niche": n_id,
                        "query_term": q_term,
                        "city": c_name,
                        "state": c_state,
                        "area": area,
                        "priority": city.get("priority", "media"),
                        "max_results": int(
                            city.get("max_results_bairro", 12)
                            if area != "_cidade"
                            else city.get("max_results_cidade", 20)
                        ),
                    }
                )

    # Mantém ordem do cities.json dentro da mesma prioridade
    city_order = {
        c.get("nome"): i for i, c in enumerate(cities) if c.get("ativo")
    }
    jobs.sort(
        key=lambda j: (
            prio_rank.get(j.get("priority", "media"), 5),
            city_order.get(j["city"], 999),
            j["niche"],
            j["area"],
        )
    )
    # Revezamento entre nichos (1 de cada, em ciclo) — evita encher um nicho e só depois o outro
    return interleave_jobs_by_niche(jobs)


def _interleave_by_key(
    jobs: list[dict[str, Any]], key_fn
) -> list[dict[str, Any]]:
    """Round-robin genérico por chave (preserva ordem de 1ª aparição)."""
    if not jobs:
        return []
    from collections import defaultdict, deque

    buckets: dict[str, deque] = defaultdict(deque)
    order: list[str] = []
    for j in jobs:
        k = key_fn(j)
        if k not in buckets:
            order.append(k)
        buckets[k].append(j)

    if len(order) <= 1:
        return list(jobs)

    out: list[dict[str, Any]] = []
    while any(buckets[k] for k in order):
        for k in order:
            if buckets[k]:
                out.append(buckets[k].popleft())
    return out


def interleave_jobs_by_niche(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Missão completa em um processo: round-robin por nicho E por cidade.

    1) Dentro de cada nicho, intercala cidades (não esgota cidade A antes da B).
    2) Depois intercala nichos: n1, n2, n3, n1, n2, ...

    Assim 4 nichos × N cidades andam juntos — não para um nicho e
    “reinicia” outro processo.
    """
    if not jobs:
        return []
    from collections import defaultdict

    by_niche: dict[str, list[dict[str, Any]]] = defaultdict(list)
    niche_order: list[str] = []
    for j in jobs:
        nid = j.get("niche") or ""
        if nid not in by_niche:
            niche_order.append(nid)
        by_niche[nid].append(j)

    if len(niche_order) <= 1 and len({j.get("city") for j in jobs}) <= 1:
        return list(jobs)

    # Por nicho: intercala cidades (e áreas já na ordem original)
    for nid in niche_order:
        by_niche[nid] = _interleave_by_key(
            by_niche[nid], lambda j: (j.get("city") or "").strip().lower()
        )

    if len(niche_order) <= 1:
        return by_niche[niche_order[0]] if niche_order else list(jobs)

    from collections import deque

    buckets = {nid: deque(by_niche[nid]) for nid in niche_order}
    out: list[dict[str, Any]] = []
    while any(buckets[n] for n in niche_order):
        for nid in niche_order:
            if buckets[nid]:
                out.append(buckets[nid].popleft())
    return out


def niche_quotas(target_leads: int, niche_ids: list[str]) -> dict[str, int]:
    """
    Divide a meta total entre os nichos o mais igual possível.
    Ex.: 20 leads e 3 nichos → 7, 7, 6.
    """
    ids = [n for n in niche_ids if n]
    # unique preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for n in ids:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    if not uniq or target_leads <= 0:
        return {n: 0 for n in uniq}
    n = len(uniq)
    base = target_leads // n
    rem = target_leads % n
    return {nid: base + (1 if i < rem else 0) for i, nid in enumerate(uniq)}
