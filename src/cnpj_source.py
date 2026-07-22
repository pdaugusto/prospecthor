"""
cnpj_source.py — Fonte B grátis (paralela ao Maps).

Sem API paga:
  1) OpenStreetMap / Overpass por cidade + tags do nicho
  2) Se o nó tiver CNPJ (tag), enriquece com BrasilAPI (grátis, rate-limit)
  3) Só grava se: sem site próprio E (telefone OU Instagram)

Nunca conta lead sem contato — mesma regra do Maps.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg2
import requests
from dotenv import load_dotenv
from loguru import logger

from src.contact import (
    enrich_contact_fields,
    has_own_website,
    has_usable_contact,
    normalize_phone,
)

load_dotenv()

_DATABASE_URL = os.getenv("DATABASE_URL", "")
_OVERPASS = os.getenv(
    "OVERPASS_URL",
    "https://overpass-api.de/api/interpreter",
)
_BRASILAPI = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"
_ROOT = Path(__file__).resolve().parent.parent

# niche_id → filtros Overpass (tags OSM)
_OSM_BY_NICHE: dict[str, list[str]] = {
    "salao_barbearia": ['["shop"="hairdresser"]', '["shop"="beauty"]', '["craft"="hairdresser"]'],
    "estetica": ['["shop"="beauty"]', '["shop"="cosmetics"]', '["amenity"="spa"]'],
    "manicure": ['["shop"="beauty"]', '["craft"="beautician"]'],
    "odontologia": ['["amenity"="dentist"]', '["healthcare"="dentist"]'],
    "clinica_medica": ['["amenity"="clinic"]', '["amenity"="doctors"]', '["healthcare"="clinic"]'],
    "fisioterapia": ['["healthcare"="physiotherapist"]', '["amenity"="clinic"]'],
    "imobiliaria": ['["office"="estate_agent"]', '["shop"="estate_agent"]'],
    "construtora": ['["office"="construction_company"]', '["craft"="builder"]'],
    "advocacia": ['["office"="lawyer"]', '["office"="notary"]'],
    "contabilidade": ['["office"="accountant"]'],
    "restaurante": ['["amenity"="restaurant"]'],
    "foodtruck": ['["amenity"="fast_food"]', '["amenity"="food_court"]'],
    "academia": ['["leisure"="fitness_centre"]', '["sport"="fitness"]'],
    "farmacia": ['["amenity"="pharmacy"]'],
    "petshop": ['["shop"="pet"]'],
    "lava_rapido": ['["amenity"="car_wash"]'],
    "oficina": ['["shop"="car_repair"]', '["craft"="car_repair"]'],
    "eletrica": ['["craft"="electrician"]', '["craft"="plumber"]'],
    "fotografia": ['["craft"="photographer"]', '["shop"="photo"]'],
    "hotel": ['["tourism"="hotel"]', '["tourism"="guest_house"]'],
    "marmore_granito": ['["shop"="stone"]', '["craft"="stonemason"]'],
    "seguradora": ['["office"="insurance"]'],
    "mentores": ['["office"="educational_institution"]', '["amenity"="college"]'],
    "comercio": ['["shop"]'],
}

_DEFAULT_OSM = ['["shop"]', '["craft"]', '["office"]', '["amenity"="clinic"]']


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _osm_filters_for_niche(niche_id: str) -> list[str]:
    nid = (niche_id or "").strip().lower()
    if nid in _OSM_BY_NICHE:
        return _OSM_BY_NICHE[nid]
    # fallback por substring
    for k, v in _OSM_BY_NICHE.items():
        if k in nid or nid in k:
            return v
    return list(_DEFAULT_OSM)


_OVERPASS_MIRRORS = [
    os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter"),
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]


def _nominatim_bbox(city: str, state: str) -> tuple[float, float, float, float] | None:
    """Retorna (south, west, north, east) ou None."""
    try:
        q = f"{city}, {state}, Brasil" if state else f"{city}, Brasil"
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": q,
                "format": "json",
                "limit": 1,
                "countrycodes": "br",
            },
            headers={"User-Agent": "ProspecTHOR/1.0 (local lead bot)"},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json() or []
        if not data:
            return None
        bb = data[0].get("boundingbox")  # [south, north, west, east] as strings
        if not bb or len(bb) < 4:
            return None
        south, north, west, east = map(float, bb[:4])
        return (south, west, north, east)
    except Exception as exc:
        logger.warning("[Fonte B] Não achei a cidade no mapa (Nominatim): %s", exc)
        return None


def _build_overpass_query(
    city: str,
    state: str,
    niche_id: str,
    limit: int = 60,
    bbox: tuple[float, float, float, float] | None = None,
) -> str:
    filters = _osm_filters_for_niche(niche_id)
    city_esc = city.replace('"', '\\"')
    parts = []
    for f in filters[:4]:
        if bbox:
            s, w, n, e = bbox
            parts.append(f"  nwr{f}({s},{w},{n},{e});")
        else:
            parts.append(f"  nwr{f}(area.searchArea);")
    body = "\n".join(parts)
    lim = max(10, min(int(limit), 120))
    if bbox:
        return f"""
[out:json][timeout:35];
(
{body}
);
out center tags {lim};
""".strip()
    return f"""
[out:json][timeout:35];
area["name"="{city_esc}"]["admin_level"~"^(4|7|8)$"]->.searchArea;
(
{body}
);
out center tags {lim};
""".strip()


def _parse_overpass_elements(elements: list) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for el in elements or []:
        tags = el.get("tags") or {}
        name = (tags.get("name") or tags.get("brand") or "").strip()
        if not name:
            continue
        phone = (
            tags.get("phone")
            or tags.get("contact:phone")
            or tags.get("contact:mobile")
            or tags.get("mobile")
            or tags.get("contact:whatsapp")
            or ""
        )
        website = (
            tags.get("website")
            or tags.get("contact:website")
            or tags.get("url")
            or ""
        )
        ig = tags.get("contact:instagram") or tags.get("instagram") or ""
        if ig and not str(ig).startswith("http"):
            ig = "https://www.instagram.com/" + str(ig).lstrip("@") + "/"
        # website que é Instagram
        if not ig and "instagram.com" in (website or "").lower():
            ig = website
        cnpj = (
            tags.get("ref:CNPJ")
            or tags.get("ref:cnpj")
            or tags.get("company:cnpj")
            or tags.get("cnpj")
            or ""
        )
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        osm_id = f"osm:{el.get('type', 'n')}/{el.get('id')}"
        out.append(
            {
                "place_id": osm_id,
                "name": name,
                "phone": normalize_phone(phone),
                "website": website,
                "instagram_url": ig if "instagram" in (ig or "").lower() else "",
                "cnpj": _digits(cnpj),
                "address": tags.get("addr:full")
                or ", ".join(
                    x
                    for x in [
                        tags.get("addr:street"),
                        tags.get("addr:housenumber"),
                        tags.get("addr:suburb"),
                    ]
                    if x
                ),
                "latitude": lat,
                "longitude": lon,
                "category": tags.get("shop") or tags.get("amenity") or tags.get("office") or "",
                "source": "osm",
                "raw_tags": tags,
            }
        )
    return out


def _overpass_search(city: str, state: str, niche_id: str, limit: int = 50) -> list[dict[str, Any]]:
    logger.info(
        "[Fonte B] Buscando no mapa (OpenStreetMap) · {} / {} · nicho {}",
        city,
        state or "?",
        niche_id,
    )
    bbox = _nominatim_bbox(city, state)
    if bbox:
        logger.info("[Fonte B] Cidade localizada no mapa — buscando empresas…")
    else:
        logger.info("[Fonte B] Usando busca por nome da cidade (pode demorar)…")

    q = _build_overpass_query(city, state, niche_id, limit=limit, bbox=bbox)
    last_err: Exception | None = None
    mirrors: list[str] = []
    for m in _OVERPASS_MIRRORS:
        if m and m not in mirrors:
            mirrors.append(m)

    for mirror in mirrors:
        try:
            r = requests.post(
                mirror,
                data={"data": q},
                timeout=40,
                headers={"User-Agent": "ProspecTHOR/1.0 (local lead bot)"},
            )
            r.raise_for_status()
            data = r.json()
            elements = data.get("elements") or []
            out = _parse_overpass_elements(elements)
            logger.info(
                "[Fonte B] Mapa devolveu {} empresas em {} / {}",
                len(out),
                city,
                niche_id,
            )
            return out
        except Exception as exc:
            last_err = exc
            logger.warning("[Fonte B] Servidor de mapa falhou ({}), tentando outro…", type(exc).__name__)
            continue

    # fallback: area query se bbox falhou
    if bbox:
        try:
            q2 = _build_overpass_query(city, state, niche_id, limit=limit, bbox=None)
            r = requests.post(
                mirrors[0] if mirrors else _OVERPASS,
                data={"data": q2},
                timeout=40,
                headers={"User-Agent": "ProspecTHOR/1.0 (local lead bot)"},
            )
            r.raise_for_status()
            out = _parse_overpass_elements((r.json() or {}).get("elements") or [])
            logger.info(
                "[Fonte B] Mapa (modo 2) devolveu {} empresas em {} / {}",
                len(out),
                city,
                niche_id,
            )
            return out
        except Exception as exc:
            last_err = exc

    logger.warning(
        "[Fonte B] Não consegui ler o mapa para {} / {}: {}",
        city,
        niche_id,
        last_err,
    )
    return []


def _brasilapi_cnpj(cnpj: str) -> dict[str, Any]:
    cnpj = _digits(cnpj)
    if len(cnpj) != 14:
        return {}
    try:
        r = requests.get(
            _BRASILAPI.format(cnpj=cnpj),
            timeout=20,
            headers={"User-Agent": "ProspecTHOR/1.0"},
        )
        if r.status_code != 200:
            return {}
        return r.json() or {}
    except Exception as exc:
        logger.debug("[FonteB/CNPJ] BrasilAPI %s: %s", cnpj, exc)
        return {}


def _enrich_with_cnpj(company: dict[str, Any]) -> dict[str, Any]:
    cnpj = company.get("cnpj") or ""
    if not cnpj:
        return company
    data = _brasilapi_cnpj(cnpj)
    if not data:
        return company
    company["source"] = "cnpj+osm" if company.get("source") == "osm" else "cnpj"
    if not company.get("phone"):
        # ddd + telefone 1
        ddd = str(data.get("ddd_telefone_1") or "")
        # às vezes vem "(79) 9999-9999"
        tel = ddd or str(data.get("telefone") or "")
        company["phone"] = normalize_phone(tel)
    if not company.get("address"):
        company["address"] = ", ".join(
            x
            for x in [
                data.get("descricao_tipo_de_logradouro"),
                data.get("logradouro"),
                data.get("numero"),
                data.get("bairro"),
                data.get("municipio"),
            ]
            if x
        )
    if data.get("nome_fantasia") and len(str(data.get("nome_fantasia"))) > 2:
        # mantém nome OSM se existir; senão fantasia
        if not company.get("name"):
            company["name"] = data.get("nome_fantasia") or data.get("razao_social")
    company["cnpj"] = cnpj
    time.sleep(0.35)  # rate limit educado
    return company


def _website_is_bad_for_lead(url: str | None) -> bool:
    """True se tem site próprio (não é lead)."""
    return has_own_website(url)


def filter_and_prepare(
    raw: list[dict[str, Any]],
    niche: str,
    city: str,
    state: str,
) -> list[dict[str, Any]]:
    """Aplica regra: sem site próprio + (tel ou IG). Enriquece CNPJ se houver."""
    kept: list[dict[str, Any]] = []
    for r in raw:
        if r.get("cnpj"):
            r = _enrich_with_cnpj(r)
        if _website_is_bad_for_lead(r.get("website")):
            continue
        # se website é IG, vira instagram
        company = {
            "place_id": r.get("place_id"),
            "name": r.get("name") or "",
            "category": r.get("category") or "",
            "niche": niche,
            "city": city,
            "state": state,
            "address": r.get("address") or "",
            "phone": r.get("phone") or "",
            "website": r.get("website") or "",
            "instagram_url": r.get("instagram_url") or "",
            "instagram_username": "",
            "rating": None,
            "review_count": 0,
            "is_open_now": None,
            "opening_hours": "",
            "latitude": r.get("latitude"),
            "longitude": r.get("longitude"),
            "maps_url": "",
            "business_status": "OPERATIONAL",
            "source": r.get("source") or "osm",
            "scraped_at": datetime.now().isoformat(),
        }
        enrich_contact_fields(company)
        if not has_usable_contact(company):
            logger.debug(
                "[Fonte B] ⏭ {} sem tel/IG — não conta",
                company.get("name"),
            )
            continue
        kept.append(company)
    return kept


def _try_enrich_google_rating(company: dict[str, Any]) -> dict[str, Any]:
    """
    Se houver GOOGLE_MAPS_API_KEY, tenta puxar nota/avaliações do Places
    (mesmo tipo de dado que o Maps usa no score). Opcional e grátis no crédito.
    """
    api_key = (os.getenv("GOOGLE_MAPS_API_KEY") or "").strip()
    if not api_key:
        return company
    name = (company.get("name") or "").strip()
    city = (company.get("city") or "").strip()
    state = (company.get("state") or "").strip()
    if not name or not city:
        return company
    try:
        from src.google_maps import PlacesAPIClient

        client = PlacesAPIClient(api_key)
        q = f"{name} {city} {state}".strip()
        raws = client.text_search(q, max_results=3)
        if not raws:
            return company
        # pega o primeiro com nome parecido
        name_l = name.lower()
        best = None
        for r in raws:
            rn = (r.get("name") or "").lower()
            if name_l in rn or rn in name_l or name_l[:8] in rn:
                best = r
                break
        if not best:
            best = raws[0]
        place_id = best.get("place_id") or ""
        details = client.get_place_details(place_id) if place_id else best
        merged = {**best, **details}
        if merged.get("rating") is not None:
            company["rating"] = merged.get("rating")
        if merged.get("user_ratings_total") is not None:
            company["review_count"] = int(merged.get("user_ratings_total") or 0)
        if not company.get("phone") and merged.get("formatted_phone_number"):
            company["phone"] = normalize_phone(merged.get("formatted_phone_number"))
        if not company.get("address") and merged.get("formatted_address"):
            company["address"] = merged.get("formatted_address")
        if merged.get("url"):
            company["maps_url"] = merged.get("url")
        # se Places trouxe site próprio, este lead não deveria ter entrado —
        # mas não descartamos aqui (já passou no filtro OSM); só anota
        w = (merged.get("website") or "").strip()
        if w and has_own_website(w):
            logger.info(
                "[Fonte B] Places achou site em {} — mantém lead OSM sem site local",
                name,
            )
        logger.info(
            "[Fonte B] Enriquecido com Google: {} · nota {} · {} reviews",
            name,
            company.get("rating"),
            company.get("review_count"),
        )
        time.sleep(0.25)
    except Exception as exc:
        logger.debug("[Fonte B] enrich Google falhou: {}", exc)
    return company


def _save_company(company: dict[str, Any], *, assign: bool = True) -> int:
    """
    Salva como o Maps: flags sem_site + IG + score com o MESMO LeadScorer.

    assign=False → sobra livre (não manda pra ninguém / não conta meta).
    """
    if not _DATABASE_URL:
        return 0
    from src.google_maps import Database

    # alinhar campos com o que o scorer/dashboard esperam (igual Maps)
    w = (company.get("website") or "").strip()
    if has_own_website(w):
        company["website_status"] = company.get("website_status") or "tem_site"
    elif w:
        company["website_status"] = "so_social"
        # social no campo website → limpa pra não confundir “tem site”
        if not has_own_website(w):
            company["website"] = w if any(
                m in w.lower() for m in ("instagram", "facebook", "linktr")
            ) else ""
    else:
        company["website_status"] = "sem_site"
        company["website"] = ""
    # garante source legível pro force-score e dashboard
    src = (company.get("source") or "osm").strip().lower()
    if src in ("", "unknown"):
        company["source"] = "osm"
    if company.get("instagram_url") or company.get("instagram_username"):
        company["instagram_status"] = "tem_instagram"
    # limpa scored_at residual se re-inserir (score_one grava de novo)
    company.pop("scored_at", None)

    # tenta nota Google (opcional) ANTES do score — mesma fórmula com mais dados
    company = _try_enrich_google_rating(company)

    db = Database()
    row_id = db.upsert_company(company)
    if not row_id:
        return 0

    # se já tem dono (upsert de existente), não vira "sobra nova" nem reassign
    already_assigned = False
    now = datetime.now().isoformat()
    try:
        conn = psycopg2.connect(_DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT assigned_to FROM companies WHERE id = %s;", (int(row_id),))
        arow = cur.fetchone()
        if arow and arow[0] is not None:
            already_assigned = True
        cur.execute(
            """
            UPDATE companies SET
                website_status = COALESCE(NULLIF(%s, ''), website_status, 'sem_site'),
                website_checked_at = COALESCE(website_checked_at, %s),
                rating = COALESCE(%s, rating),
                review_count = COALESCE(%s, review_count),
                maps_url = COALESCE(NULLIF(%s, ''), maps_url),
                address = COALESCE(NULLIF(%s, ''), address),
                phone = COALESCE(NULLIF(%s, ''), phone),
                instagram_url = COALESCE(NULLIF(%s, ''), instagram_url),
                instagram_username = COALESCE(NULLIF(%s, ''), instagram_username),
                instagram_status = CASE
                    WHEN COALESCE(NULLIF(%s, ''), '') <> '' OR COALESCE(NULLIF(%s, ''), '') <> ''
                    THEN 'tem_instagram'
                    ELSE COALESCE(instagram_status, NULL)
                END
            WHERE id = %s;
            """,
            (
                company.get("website_status") or "sem_site",
                now,
                company.get("rating"),
                company.get("review_count"),
                company.get("maps_url") or "",
                company.get("address") or "",
                company.get("phone") or "",
                company.get("instagram_url") or "",
                company.get("instagram_username") or "",
                company.get("instagram_url") or "",
                company.get("instagram_username") or "",
                int(row_id),
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        logger.warning("[Fonte B] pós-save flags id={}: {}", row_id, exc)

    # MESMO score do Maps (LeadScorer.score_one)
    # sobra: assign=False; se já tinha dono, também não reassign
    do_assign = bool(assign) and not already_assigned
    try:
        from src.scorer import LeadScorer

        scorer = LeadScorer()
        scorer.db.ensure_sem_site_flags(int(row_id))
        result = scorer.score_one(int(row_id), assign=do_assign)
        if result:
            logger.info(
                "[Fonte B] Score (igual Maps): {} → {} pts · {}{}",
                company.get("name"),
                result.get("lead_score"),
                result.get("lead_class"),
                " · SOBRA" if not do_assign else "",
            )
    except Exception as exc:
        logger.warning("[Fonte B] score id={}: {}", row_id, exc)
    return int(row_id or 0)


def _try_save_with_shared_meta(company: dict[str, Any], niche: str | None = None) -> int:
    """
    Grava lead na meta TOTAL compartilhada (Maps + Fonte B) se couber.
    Sem cota por nicho — pode encher de um nicho só.

    Se meta total cheia → grava como SOBRA (sem dono), não joga fora.
    """
    from src.bot_status import (
        try_claim_one_lead,
        release_one_lead,
        should_stop_for_meta,
    )

    n_id = niche or company.get("niche") or ""
    try:
        ok, total, tgt = try_claim_one_lead(niche=n_id or None)
    except Exception:
        ok, total, tgt = True, 0, 0
    if not ok:
        try:
            rid = _save_company(company, assign=False)
        except Exception:
            rid = 0
        if rid:
            logger.info(
                "[Fonte B] 📦 SOBRA (meta {}/{} cheia): {} — sem dono, fica no pool",
                total,
                tgt or "∞",
                company.get("name"),
            )
            company["_saved_as_sobra"] = True
            return rid
        return 0
    try:
        rid = _save_company(company, assign=True)
        if not rid:
            release_one_lead(niche=n_id or None)
            return 0
        logger.info(
            "[Fonte B] Meta compartilhada agora: {}/{}",
            total,
            tgt or "∞",
        )
        company["_saved_as_sobra"] = False
        return rid
    except Exception:
        try:
            release_one_lead(niche=n_id or None)
        except Exception:
            pass
        raise


def run_fonte_b_for_plan(
    cities: list[dict[str, Any]],
    niches: list[dict[str, Any]],
    target_leads: int = 0,
    max_per_pair: int = 25,
) -> dict[str, Any]:
    """
    Varre cidade × nicho do plano. Meta TOTAL COMPARTILHADA com o Maps
    (bot_runtime.session_leads_count + mission_target).

    SEM cota por nicho: pode encher de um nicho só — mais rápido.
    Extras além da meta viram sobra (sem dono).
    """
    from src.bot_status import (
        get_session_leads,
        set_mission_meta,
        remaining_to_meta,
        should_stop_for_meta,
    )

    stats = {
        "saved": 0,
        "sobras": 0,
        "skipped_no_contact": 0,
        "skipped_has_site": 0,
        "raw_found": 0,
        "pairs": 0,
        "errors": 0,
        "stopped_meta": False,
    }
    total_target = int(target_leads or 0)

    active_cities = []
    for city in cities:
        if city.get("ativo") is False:
            continue
        c_name = city.get("nome") or city.get("id") or ""
        if c_name:
            active_cities.append(city)

    # ordem natural dos nichos — SEM inverter, SEM cota por nicho
    niche_list = [n for n in niches if n.get("id")]

    if total_target > 0:
        set_mission_meta(total_target, reset_leads=False)

    shared0 = get_session_leads()
    logger.info("[Fonte B] ========== INÍCIO ==========")
    logger.info(
        "[Fonte B] Cidades: {} | Nichos: {} | Meta TOTAL: {}/{} "
        "(Maps+Fonte B, SEM divisão por nicho — pode encher de qualquer um)",
        len(active_cities),
        len(niche_list),
        shared0,
        total_target or "∞",
    )
    if niche_list:
        logger.info(
            "[Fonte B] Ordem: {}",
            " → ".join(n.get("id") or "?" for n in niche_list),
        )

    if not active_cities or not niche_list:
        logger.warning(
            "[Fonte B] Nada para buscar — plano sem cidade ou sem nicho. "
            "Confira a missão no cockpit."
        )
        return stats

    if total_target and should_stop_for_meta():
        logger.info(
            "[Fonte B] 🛑 META BATEU ({}/{}) — Fonte B PARA (já completa).",
            get_session_leads(),
            total_target,
        )
        stats["stopped_meta"] = True
        return stats

    for city in active_cities:
        c_name = city.get("nome") or city.get("id") or ""
        c_state = city.get("estado") or ""
        for niche in niche_list:
            if total_target and should_stop_for_meta():
                stats["stopped_meta"] = True
                logger.info(
                    "[Fonte B] 🛑 META BATEU ({}/{}) — Fonte B PARA (os 2 param).",
                    get_session_leads(),
                    total_target,
                )
                break
            n_id = niche.get("id") or ""
            if not n_id:
                continue

            stats["pairs"] += 1
            rem = remaining_to_meta()
            if rem is not None:
                if rem <= 0:
                    stats["stopped_meta"] = True
                    break
                remain = max(1, min(max_per_pair, rem))
            else:
                remain = max_per_pair

            shared = get_session_leads()
            logger.info(
                "[Fonte B] → {} / {} · pede até {} · meta {}/{}",
                c_name,
                n_id,
                remain,
                shared,
                total_target or "∞",
            )
            try:
                raw = _overpass_search(c_name, c_state, n_id, limit=max(remain * 3, 40))
                stats["raw_found"] += len(raw)
                has_site_n = 0
                for r in raw:
                    if _website_is_bad_for_lead(r.get("website")):
                        has_site_n += 1
                stats["skipped_has_site"] += has_site_n
                prepared = filter_and_prepare(raw, n_id, c_name, c_state)
                no_contact = max(0, len(raw) - has_site_n - len(prepared))
                stats["skipped_no_contact"] += no_contact
                logger.info(
                    "[Fonte B] {}/{}: {} no mapa · {} com site · {} sem contato · {} prontos",
                    c_name,
                    n_id,
                    len(raw),
                    has_site_n,
                    no_contact,
                    len(prepared),
                )
                if not raw:
                    logger.warning(
                        "[Fonte B] Zero no mapa para {} / {}. "
                        "OpenStreetMap pode estar vazio nesse nicho/cidade.",
                        c_name,
                        n_id,
                    )
                elif not prepared:
                    logger.warning(
                        "[Fonte B] Achei {} no mapa, mas nenhum com celular/WA ou Instagram "
                        "(e sem site). Por isso não grava lead.",
                        len(raw),
                    )
                saved_here = 0
                sobras_here = 0
                dump_sobras = False
                if total_target and should_stop_for_meta():
                    stats["stopped_meta"] = True
                    dump_sobras = True
                    logger.info(
                        "[Fonte B] 🛑 META {}/{} — lote de {} vira SOBRA (sem dono)",
                        get_session_leads(),
                        total_target,
                        len(prepared),
                    )

                for company in prepared:
                    try:
                        if not dump_sobras:
                            if total_target and should_stop_for_meta():
                                stats["stopped_meta"] = True
                                dump_sobras = True
                                logger.info(
                                    "[Fonte B] 🛑 META BATEU {}/{} — resto do lote → SOBRAS",
                                    get_session_leads(),
                                    total_target,
                                )
                            elif saved_here >= remain:
                                # teto deste par na meta → sobras o resto do lote
                                dump_sobras = True

                        if dump_sobras:
                            rid = _save_company(company, assign=False)
                            if rid:
                                stats["sobras"] += 1
                                sobras_here += 1
                                logger.info(
                                    "[Fonte B] 📦 SOBRA: {} · {} · {}",
                                    company.get("name"),
                                    c_name,
                                    n_id,
                                )
                            continue

                        rid = _try_save_with_shared_meta(company, niche=n_id)
                        if rid:
                            if company.get("_saved_as_sobra"):
                                stats["sobras"] += 1
                                sobras_here += 1
                                dump_sobras = True
                                if total_target and should_stop_for_meta():
                                    stats["stopped_meta"] = True
                            else:
                                stats["saved"] += 1
                                saved_here += 1
                                logger.info(
                                    "[Fonte B] ✓ SALVO: {} · {} · canal {} · meta {}/{}",
                                    company.get("name"),
                                    c_name,
                                    company.get("contact_channel") or "?",
                                    get_session_leads(),
                                    total_target or "∞",
                                )
                                if total_target and should_stop_for_meta():
                                    stats["stopped_meta"] = True
                                    dump_sobras = True
                                    logger.info(
                                        "[Fonte B] 🛑 META BATEU {}/{} — resto → SOBRAS",
                                        get_session_leads(),
                                        total_target,
                                    )
                    except Exception as exc:
                        stats["errors"] += 1
                        logger.warning("[Fonte B] Erro ao salvar {}: {}", company.get("name"), exc)
                logger.info(
                    "[Fonte B] Par {}/{}: +{} missão · +{} sobras · meta {}/{}",
                    c_name,
                    n_id,
                    saved_here,
                    sobras_here,
                    get_session_leads(),
                    total_target or "∞",
                )
                if stats["stopped_meta"]:
                    break
                time.sleep(1.2)
            except Exception as exc:
                stats["errors"] += 1
                logger.warning("[Fonte B] Falha em {}/{}: {}", c_name, n_id, exc)
        if total_target and should_stop_for_meta():
            stats["stopped_meta"] = True
            break

    logger.info(
        "[Fonte B] ========== FIM ========== "
        "missão={} | sobras={} | meta={}/{} | no mapa={} | "
        "sem contato={} | erros={}",
        stats["saved"],
        stats.get("sobras", 0),
        get_session_leads(),
        total_target or "∞",
        stats["raw_found"],
        stats["skipped_no_contact"],
        stats["errors"],
    )
    if stats.get("sobras"):
        logger.info(
            "[Fonte B] 📦 {} lead(s) nas SOBRAS (sem dono) — não foram jogados fora.",
            stats["sobras"],
        )
    if stats["stopped_meta"]:
        logger.info(
            "[Fonte B] 🛑 META BATEU — missão ok; extras foram p/ sobras se havia lote."
        )
    if stats["saved"] == 0 and not stats["stopped_meta"] and not stats.get("sobras"):
        logger.warning(
            "[Fonte B] Nenhum lead novo. Motivos comuns: "
            "mapa vazio no nicho, empresas só com site, ou sem celular/Instagram no OpenStreetMap. "
            "O Maps (Google) continua sendo a fonte principal."
        )
    return stats


def run_from_bot_plan() -> dict[str, Any]:
    """Lê plano do painel/cockpit e roda Fonte B."""
    from src.bot_plan import get_plan, apply_plan_to_job_sources

    niches_path = _ROOT / "config" / "niches.json"
    cities_path = _ROOT / "config" / "cities.json"
    with open(niches_path, "r", encoding="utf-8") as f:
        niches_data = json.load(f).get("nichos", [])
    with open(cities_path, "r", encoding="utf-8") as f:
        cities_data = json.load(f).get("cidades", [])
    plan = get_plan()
    cities_data, niches_data, target = apply_plan_to_job_sources(
        cities_data, niches_data, plan
    )
    logger.info(
        "[Fonte B] Plano do cockpit: meta={} | {} cidade(s) | nichos={}",
        target or "∞",
        len(cities_data),
        ", ".join(n.get("id") or "?" for n in niches_data) or "(nenhum)",
    )
    if not cities_data or not niches_data:
        logger.error(
            "[Fonte B] Plano vazio — a missão precisa de cidade e nicho antes de rodar."
        )
        return {
            "saved": 0,
            "skipped_no_contact": 0,
            "skipped_has_site": 0,
            "raw_found": 0,
            "pairs": 0,
            "errors": 1,
            "message": "plano vazio",
        }
    return run_fonte_b_for_plan(
        cities_data,
        niches_data,
        target_leads=int(target or 0),
        max_per_pair=20,
    )
