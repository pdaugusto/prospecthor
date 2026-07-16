"""
checkpoint.py — Checkpoint por EMPRESA (não por região)

A fonte da verdade é o banco (Supabase): se o place_id já existe,
a empresa já foi vista e o bot não gasta tempo reabrindo o painel.

Este módulo carrega os place_ids conhecidos em memória (set) para
consultas O(1) durante o scraping.
"""

from __future__ import annotations

import os
from typing import Iterable

import psycopg2
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

_DATABASE_URL: str = os.getenv("DATABASE_URL", "")


class CompanyCheckpoint:
    """
    Cache em memória dos place_ids já processados.

    Uso:
        cp = CompanyCheckpoint.load()
        if cp.seen(place_id):
            skip
        cp.add(place_id)  # após salvar nova empresa
    """

    def __init__(self, place_ids: set[str] | None = None) -> None:
        self._ids: set[str] = set(place_ids or [])

    @classmethod
    def load(cls) -> "CompanyCheckpoint":
        """Carrega todos os place_ids existentes no banco."""
        if not _DATABASE_URL:
            logger.warning("[Checkpoint] DATABASE_URL ausente — checkpoint vazio.")
            return cls()
        try:
            conn = psycopg2.connect(_DATABASE_URL)
            cur = conn.cursor()
            cur.execute(
                "SELECT place_id FROM companies WHERE place_id IS NOT NULL AND place_id <> '';"
            )
            ids = {row[0] for row in cur.fetchall() if row[0]}
            cur.close()
            conn.close()
            logger.info(f"[Checkpoint] {len(ids)} empresas já no banco (puláveis).")
            return cls(ids)
        except Exception as exc:
            logger.warning(f"[Checkpoint] Falha ao carregar place_ids: {exc}")
            return cls()

    def seen(self, place_id: str | None) -> bool:
        if not place_id:
            return False
        return place_id in self._ids

    def add(self, place_id: str | None) -> None:
        if place_id:
            self._ids.add(place_id)

    def add_many(self, place_ids: Iterable[str]) -> None:
        for pid in place_ids:
            self.add(pid)

    def as_set(self) -> set[str]:
        """Cópia do conjunto de place_ids conhecidos."""
        return set(self._ids)

    def __len__(self) -> int:
        return len(self._ids)
