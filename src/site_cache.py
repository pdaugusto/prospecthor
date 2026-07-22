"""
site_cache.py — place_ids que JÁ têm site próprio (nunca reabrir no Maps).

Persistido em data/known_has_site.json (local, grátis).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from loguru import logger

_PATH = Path(__file__).resolve().parent.parent / "data" / "known_has_site.json"
_ids: set[str] | None = None


def _load() -> set[str]:
    global _ids
    if _ids is not None:
        return _ids
    if not _PATH.exists():
        _ids = set()
        return _ids
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _ids = set(data.get("place_ids") or [])
        logger.debug("[SiteCache] %s place_ids com site em cache", len(_ids))
    except Exception as exc:
        logger.warning("[SiteCache] falha ao ler: %s", exc)
        _ids = set()
    return _ids


def _save() -> None:
    ids = _load()
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {
                "place_ids": sorted(ids),
                "count": len(ids),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )


def has_site(place_id: str | None) -> bool:
    if not place_id:
        return False
    return place_id in _load()


def mark_has_site(place_id: str | None) -> None:
    if not place_id:
        return
    ids = _load()
    if place_id in ids:
        return
    ids.add(place_id)
    try:
        _save()
    except Exception as exc:
        logger.warning("[SiteCache] falha ao gravar: %s", exc)


def mark_many(place_ids: Iterable[str]) -> None:
    ids = _load()
    n0 = len(ids)
    for p in place_ids:
        if p:
            ids.add(p)
    if len(ids) != n0:
        try:
            _save()
        except Exception as exc:
            logger.warning("[SiteCache] falha ao gravar: %s", exc)


def as_set() -> set[str]:
    return set(_load())
