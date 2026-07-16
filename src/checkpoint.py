"""
checkpoint.py — Progresso do lote Brasil (cidade + nicho)

Evita refazer combinações já concluídas ao reiniciar `python main.py run`.

Arquivo: data/pipeline_checkpoint.json
Chave: "niche|city|state" (ex: "odontologia|São Paulo|SP")
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

_BASE = Path(__file__).resolve().parent.parent
_CHECKPOINT_PATH = _BASE / "data" / "pipeline_checkpoint.json"


def _key(niche: str, city: str, state: str) -> str:
    return f"{(niche or '').strip().lower()}|{(city or '').strip().lower()}|{(state or '').strip().upper()}"


def load_checkpoint() -> dict[str, Any]:
    """Carrega o arquivo de checkpoint (ou estrutura vazia)."""
    if not _CHECKPOINT_PATH.exists():
        return {"completed": {}, "updated_at": None}
    try:
        with open(_CHECKPOINT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "completed" not in data or not isinstance(data["completed"], dict):
            data["completed"] = {}
        return data
    except Exception as exc:
        logger.warning(f"[Checkpoint] Falha ao ler {_CHECKPOINT_PATH}: {exc}")
        return {"completed": {}, "updated_at": None}


def save_checkpoint(data: dict[str, Any]) -> None:
    """Persiste o checkpoint em disco."""
    _CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now().isoformat()
    with open(_CHECKPOINT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_done(niche: str, city: str, state: str) -> bool:
    """True se essa combinação cidade+nicho já foi concluída."""
    data = load_checkpoint()
    return _key(niche, city, state) in data.get("completed", {})


def mark_done(niche: str, city: str, state: str, items_found: int = 0) -> None:
    """Marca combinação como concluída após pipeline com sucesso."""
    data = load_checkpoint()
    data.setdefault("completed", {})[_key(niche, city, state)] = {
        "niche": niche,
        "city": city,
        "state": state,
        "items_found": items_found,
        "completed_at": datetime.now().isoformat(),
    }
    save_checkpoint(data)
    logger.info(f"[Checkpoint] ✓ Concluído: {niche} | {city}-{state}")


def list_completed() -> list[dict[str, Any]]:
    data = load_checkpoint()
    return list(data.get("completed", {}).values())


def seed_defaults() -> None:
    """
    Marca combinações que o operador já rodou manualmente.
    Idempotente: não sobrescreve timestamps existentes.
    """
    # SP odontologia + advocacia já feitos (operador parou antes do RJ)
    defaults = [
        ("odontologia", "São Paulo", "SP"),
        ("advocacia", "São Paulo", "SP"),
    ]
    data = load_checkpoint()
    changed = False
    for niche, city, state in defaults:
        k = _key(niche, city, state)
        if k not in data["completed"]:
            data["completed"][k] = {
                "niche": niche,
                "city": city,
                "state": state,
                "items_found": 0,
                "completed_at": datetime.now().isoformat(),
                "seeded": True,
            }
            changed = True
            logger.info(f"[Checkpoint] Seed: {niche} | {city}-{state}")
    if changed:
        save_checkpoint(data)
