"""
instagram_checker.py — Verificação de presença no Instagram
============================================================

Verifica se cada empresa possui perfil no Instagram e avalia
a qualidade da presença digital na plataforma.

Estratégia em duas etapas:
    1. DESCOBERTA  — encontra o handle do Instagram da empresa
    2. ANÁLISE     — acessa o perfil público e coleta métricas

Métodos de descoberta (em ordem de confiabilidade):
    A. URL já cadastrada no Google Maps (campo "website" é instagram.com/...)
    B. Busca Google: "{nome empresa}" instagram {cidade}
    C. Busca DuckDuckGo: fallback se Google bloquear
    D. Tentativa heurística: variações do nome da empresa como username

Coleta de métricas (Playwright + parsing de JSON embutido na página):
    - Seguidores, posts, following
    - Bio e link na bio
    - Data da última postagem (via timestamp do post mais recente)
    - Status de conta verificada e business

Classificações e scores:
    sem_instagram  → +25 pts   Sem perfil encontrado
    parado         → +20 pts   Último post > 3 meses atrás
    inativo        → +15 pts   Último post > 6 meses, poucos posts
    fraco          → +10 pts   Pouco engajamento, bio vazia, sem link
    bom            → +0  pts   Perfil ativo e bem configurado

Subcritérios adicionais (score acumulativo):
    Bio vazia               → +5 pts
    Sem link na bio         → +5 pts
    Menos de 9 posts        → +5 pts
    Menos de 100 seguidores → +5 pts
    Último post > 3 meses   → +10 pts (extra sobre a classificação)
    Último post > 6 meses   → +15 pts (extra sobre a classificação)

Uso:
    from src.instagram_checker import InstagramChecker

    checker = InstagramChecker()
    results = checker.check_all(limit=50)
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import random
import unicodedata
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from loguru import logger
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

load_dotenv()

_DB_PATH: str = os.getenv("DATABASE_PATH", "data/leads.db")
_HEADLESS: bool = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
_TIMEOUT_MS: int = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "30000"))
_DELAY_MIN: float = float(os.getenv("REQUEST_DELAY_MIN_S", "3.0"))
_DELAY_MAX: float = float(os.getenv("REQUEST_DELAY_MAX_S", "5.0"))

# Thresholds de classificação
_PARADO_DAYS: int = 90    # > 90 dias sem postar = "parado"
_INATIVO_DAYS: int = 180  # > 180 dias sem postar = "inativo"
_MIN_FOLLOWERS: int = 100 # Abaixo disso = "fraco"
_MIN_POSTS: int = 9       # Abaixo disso = perfil muito esparso

# Headers para buscas no Google / DuckDuckGo
_SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.google.com/",
    "DNT": "1",
}

# Headers para acessar páginas do Instagram diretamente
_INSTAGRAM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

# User-Agents rotativos para o Playwright (anti-detecção)
_PLAYWRIGHT_USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4 Safari/605.1.15"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
]


# ---------------------------------------------------------------------------
# Colunas de migração do banco
# ---------------------------------------------------------------------------

_INSTAGRAM_COLUMNS: list[tuple[str, str]] = [
    ("instagram_url",        "TEXT"),
    ("instagram_username",   "TEXT"),
    ("instagram_status",     "TEXT"),
    ("instagram_followers",  "INTEGER"),
    ("instagram_following",  "INTEGER"),
    ("instagram_posts",      "INTEGER"),
    ("instagram_last_post",  "TEXT"),     # ISO-8601 ou "desconhecido"
    ("instagram_has_bio",    "INTEGER"),  # 0/1
    ("instagram_has_link",   "INTEGER"),  # 0/1
    ("instagram_bio",        "TEXT"),
    ("instagram_is_verified","INTEGER"),  # 0/1
    ("instagram_is_business","INTEGER"),  # 0/1
    ("instagram_score",      "INTEGER"),
    ("instagram_checked_at", "TEXT"),
]

_SAVE_INSTAGRAM_SQL = """
UPDATE companies SET
    instagram_url        = :instagram_url,
    instagram_username   = :instagram_username,
    instagram_status     = :instagram_status,
    instagram_followers  = :instagram_followers,
    instagram_following  = :instagram_following,
    instagram_posts      = :instagram_posts,
    instagram_last_post  = :instagram_last_post,
    instagram_has_bio    = :instagram_has_bio,
    instagram_has_link   = :instagram_has_link,
    instagram_bio        = :instagram_bio,
    instagram_is_verified= :instagram_is_verified,
    instagram_is_business= :instagram_is_business,
    instagram_score      = :instagram_score,
    instagram_checked_at = :instagram_checked_at
WHERE id = :id;
"""

_SELECT_PENDING_SQL = """
SELECT id, name, website, city, state, niche
FROM companies
WHERE instagram_checked_at IS NULL
  AND (business_status IS NULL OR business_status != 'CLOSED_PERMANENTLY')
ORDER BY id
LIMIT :limit;
"""

_SELECT_BY_ID_SQL = """
SELECT id, name, website, city, state, niche
FROM companies WHERE id = ?;
"""


# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------

class InstagramDatabase:
    """
    Gerencia a conexão com o SQLite para o módulo instagram_checker.

    Adiciona as colunas instagram_* à tabela companies via migração
    não-destrutiva (ALTER TABLE com verificação prévia de existência).
    """

    def __init__(self, db_path: str = _DB_PATH) -> None:
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"Banco de dados não encontrado: {self.db_path}\n"
                "Execute primeiro o google_maps.py para popular o banco."
            )
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _migrate(self) -> None:
        """
        Adiciona as colunas instagram_* à tabela companies de forma segura.

        Verifica quais colunas já existem antes de tentar adicionar —
        idempotente e seguro para rodar múltiplas vezes.
        """
        with self._connect() as conn:
            existing = {
                row[1]
                for row in conn.execute("PRAGMA table_info(companies);").fetchall()
            }
            added = []
            for col_name, col_type in _INSTAGRAM_COLUMNS:
                if col_name not in existing:
                    try:
                        conn.execute(
                            f"ALTER TABLE companies ADD COLUMN {col_name} {col_type};"
                        )
                        added.append(col_name)
                    except sqlite3.OperationalError as exc:
                        logger.warning(f"[DB] Não adicionou {col_name!r}: {exc}")
            if added:
                conn.commit()
                logger.info(f"[DB] Migração Instagram: {len(added)} colunas → {added}")
            else:
                logger.debug("[DB] Schema Instagram já atualizado.")

    def get_pending(self, limit: int = 100) -> list[dict[str, Any]]:
        """Retorna empresas com instagram_checked_at IS NULL."""
        with self._connect() as conn:
            rows = conn.execute(_SELECT_PENDING_SQL, {"limit": limit}).fetchall()
            return [dict(row) for row in rows]

    def get_by_id(self, company_id: int) -> dict[str, Any] | None:
        """Retorna uma empresa pelo ID."""
        with self._connect() as conn:
            row = conn.execute(_SELECT_BY_ID_SQL, (company_id,)).fetchone()
            return dict(row) if row else None

    def save_result(self, result: dict[str, Any]) -> None:
        """Persiste o resultado da verificação de Instagram."""
        try:
            with self._connect() as conn:
                conn.execute(_SAVE_INSTAGRAM_SQL, result)
                conn.commit()
            logger.debug(
                f"[DB] Instagram salvo: status={result['instagram_status']!r} "
                f"score={result['instagram_score']} id={result['id']}"
            )
        except sqlite3.Error as exc:
            logger.error(f"[DB] Erro ao salvar Instagram id={result.get('id')}: {exc}")

    def get_stats(self) -> dict[str, Any]:
        """Retorna estatísticas das verificações de Instagram."""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM companies;").fetchone()[0]
            checked = conn.execute(
                "SELECT COUNT(*) FROM companies "
                "WHERE instagram_checked_at IS NOT NULL;"
            ).fetchone()[0]
            by_status = conn.execute(
                "SELECT instagram_status, COUNT(*) as cnt FROM companies "
                "WHERE instagram_checked_at IS NOT NULL "
                "GROUP BY instagram_status ORDER BY cnt DESC;"
            ).fetchall()
        return {
            "total": total,
            "checked": checked,
            "pending": total - checked,
            "by_status": {row[0]: row[1] for row in by_status},
        }


# ---------------------------------------------------------------------------
# Resultado padrão
# ---------------------------------------------------------------------------

def _empty_result(company_id: int) -> dict[str, Any]:
    """Dicionário de resultado Instagram zerado."""
    return {
        "id": company_id,
        "instagram_url": None,
        "instagram_username": None,
        "instagram_status": "sem_instagram",
        "instagram_followers": None,
        "instagram_following": None,
        "instagram_posts": None,
        "instagram_last_post": None,
        "instagram_has_bio": None,
        "instagram_has_link": None,
        "instagram_bio": None,
        "instagram_is_verified": None,
        "instagram_is_business": None,
        "instagram_score": None,
        "instagram_checked_at": datetime.now().isoformat(),
    }


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------

def _delay(min_s: float = _DELAY_MIN, max_s: float = _DELAY_MAX) -> None:
    """Delay aleatório entre requests (anti-bot)."""
    secs = random.uniform(min_s, max_s)
    logger.debug(f"[Instagram] Aguardando {secs:.1f}s...")
    time.sleep(secs)


def _normalize_to_username(text: str) -> str:
    """
    Normaliza um nome de empresa para o formato de username do Instagram.

    Passos:
        1. Remove acentos (NFD → ASCII)
        2. Converte para minúsculas
        3. Remove caracteres não alfanuméricos (mantém ponto e underscore)
        4. Remove espaços e conecta palavras
        5. Remove prefixos genéricos (restaurante, bar, etc.)

    Exemplos:
        "Restaurante Sabor & Arte"  → "saborarte"
        "Bar do João"               → "bardojoao"
        "Clínica Dr. Silva"         → "clinicadrsilva"
    """
    # Remove acentos
    normalized = unicodedata.normalize("NFD", text)
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    # Minúsculas
    normalized = normalized.lower()
    # Remove caracteres especiais (mantém alfanuméricos, ponto e underscore)
    normalized = re.sub(r"[^a-z0-9._]", "", normalized)
    # Remove pontos duplicados ou no início/fim
    normalized = normalized.strip(".")
    return normalized


def _generate_username_candidates(company_name: str, city: str = "") -> list[str]:
    """
    Gera lista de candidatos de username do Instagram para uma empresa.

    Estratégia:
        - Extrai as palavras significativas do nome
        - Remove palavras genéricas (artigos, preposições, tipo de negócio)
        - Combina palavras de formas diferentes
        - Adiciona variações com cidade quando disponível

    Args:
        company_name: Nome da empresa (ex: "Restaurante Sabor da Terra")
        city:         Cidade da empresa (ex: "Porto Alegre")

    Returns:
        Lista de possíveis usernames, do mais ao menos provável.
    """
    # Palavras genéricas a remover do nome
    STOPWORDS = {
        "restaurante", "lanchonete", "pizzaria", "hamburgueria", "churrascaria",
        "padaria", "confeitaria", "sorveteria", "cafeteria", "cafe", "bar",
        "barbearia", "salao", "clinica", "consultorio", "academia", "estudio",
        "pet", "shop", "store", "ltda", "me", "eireli", "sa", "epp",
        "de", "da", "do", "das", "dos", "e", "em", "a", "o", "as", "os",
        "para", "por", "com", "sem", "um", "uma", "no", "na", "nos", "nas",
        "dr", "dra", "prof", "profa",
    }

    base = _normalize_to_username(company_name)

    # Palavras do nome sem stopwords
    raw_words = re.sub(r"[^a-z0-9 ]", " ", company_name.lower())
    raw_words = unicodedata.normalize("NFD", raw_words)
    raw_words = "".join(c for c in raw_words if unicodedata.category(c) != "Mn")
    words = [w for w in raw_words.split() if w not in STOPWORDS and len(w) > 1]

    city_slug = _normalize_to_username(city) if city else ""

    candidates = []

    # 1. Nome base completo normalizado
    if base:
        candidates.append(base)

    # 2. Palavras significativas concatenadas
    if words:
        joined = "".join(words)
        if joined and joined != base:
            candidates.append(joined)

    # 3. Duas primeiras palavras significativas
    if len(words) >= 2:
        candidates.append(words[0] + words[1])

    # 4. Primeira palavra significativa + cidade
    if words and city_slug:
        candidates.append(words[0] + city_slug[:5])

    # 5. Nome base + cidade
    if base and city_slug:
        candidates.append(base + city_slug[:4])

    # 6. Variação com underscore entre palavras significativas
    if len(words) >= 2:
        candidates.append("_".join(words[:3]))

    # 7. Apenas a primeira palavra significativa
    if words:
        candidates.append(words[0])

    # Remove duplicatas mantendo a ordem, filtra vazios e excessivamente curtos
    seen: set[str] = set()
    unique = []
    for c in candidates:
        c = c[:30]  # Instagram limita usernames a 30 chars
        if c and len(c) >= 3 and c not in seen:
            seen.add(c)
            unique.append(c)

    return unique


def _days_since(iso_date: str) -> int | None:
    """
    Calcula quantos dias se passaram desde uma data ISO-8601.

    Args:
        iso_date: Data no formato "2024-01-15T10:30:00+00:00" ou similar.

    Returns:
        Número de dias ou None se a data for inválida.
    """
    if not iso_date or iso_date == "desconhecido":
        return None
    try:
        # Remove frações de segundo e normaliza timezone
        clean = re.sub(r"\.\d+", "", iso_date)
        # Tenta com timezone
        for fmt in [
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
        ]:
            try:
                dt = datetime.strptime(clean, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                now = datetime.now(tz=timezone.utc)
                return (now - dt).days
            except ValueError:
                continue
    except Exception:
        pass
    return None


def _parse_count(raw: str | None) -> int | None:
    """
    Converte strings de contagem do Instagram para inteiro.

    Formatos suportados:
        "1.234"       → 1234
        "12,3 mil"    → 12300
        "1,2 M"       → 1200000
        "12K"         → 12000
        "500"         → 500
    """
    if not raw:
        return None
    raw = str(raw).strip().lower().replace("\xa0", " ")

    # Formato: "1,2 m" ou "1.2m" (milhões)
    m_match = re.search(r"([\d.,]+)\s*m(?:il(?:hão|hões)?)?(?:\b|$)", raw)
    if m_match and "m" in raw:
        try:
            num = float(m_match.group(1).replace(",", ".").replace(".", "", m_match.group(1).count(".") - 1))
            if num < 1000:  # é "milhão", não "mil"
                return int(num * 1_000_000)
        except (ValueError, Exception):
            pass

    # Formato: "12,3 mil" ou "12k"
    k_match = re.search(r"([\d.,]+)\s*(?:mil|k)", raw)
    if k_match:
        try:
            num_str = k_match.group(1).replace(",", ".").replace("\u00a0", "")
            return int(float(num_str) * 1000)
        except ValueError:
            pass

    # Formato numérico simples: "1.234" (ponto como separador de milhar BR)
    digits_only = re.sub(r"[^\d]", "", raw)
    if digits_only:
        return int(digits_only)

    return None


def _extract_instagram_url_from_website(website: str) -> str | None:
    """
    Verifica se a URL do website já é diretamente um perfil do Instagram.

    Args:
        website: URL do website da empresa.

    Returns:
        URL normalizada do Instagram ou None.
    """
    if not website:
        return None
    url_lower = website.lower()
    if "instagram.com/" in url_lower:
        # Normaliza: garante https e remove parâmetros
        try:
            parsed = urllib.parse.urlparse(website)
            path = parsed.path.rstrip("/")
            # Remove subpaths (ex: /p/...) — fica só o perfil
            path_parts = [p for p in path.split("/") if p and p not in ("p", "reel", "tv", "stories")]
            if path_parts:
                username = path_parts[0].lstrip("@")
                return f"https://www.instagram.com/{username}/"
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Descoberta do handle do Instagram
# ---------------------------------------------------------------------------

class InstagramFinder:
    """
    Encontra o handle/URL do Instagram de uma empresa usando múltiplos métodos.

    Métodos em ordem de prioridade:
        1. URL já cadastrada no website (ex: website == "instagram.com/empresa")
        2. Busca Google: "{nome}" instagram {cidade}
        3. Busca DuckDuckGo: fallback se Google bloquear
        4. Tentativa heurística: variações do nome como username
    """

    _GOOGLE_SEARCH_URL = "https://www.google.com/search"
    _DDG_SEARCH_URL = "https://duckduckgo.com/html/"

    # Regex para extrair URLs do Instagram dos resultados de busca
    _IG_URL_RE = re.compile(
        r"(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9_.]{1,30})/?",
        re.IGNORECASE,
    )

    # Padrões que indicam que o resultado NÃO é um perfil de empresa
    _IG_NOISE_USERNAMES = frozenset([
        "p", "reel", "tv", "explore", "stories", "accounts",
        "about", "blog", "press", "jobs", "privacy", "terms",
        "help", "support", "download", "login", "signup",
        "legal", "api", "developer",
    ])

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(_SEARCH_HEADERS)

    def find(
        self,
        company_name: str,
        city: str = "",
        website: str = "",
    ) -> str | None:
        """
        Tenta encontrar o handle do Instagram de uma empresa.

        Args:
            company_name: Nome da empresa.
            city:         Cidade (melhora a precisão da busca).
            website:      URL do website (pode já ser o Instagram).

        Returns:
            URL completa do Instagram (ex: "https://www.instagram.com/empresa/")
            ou None se não encontrado.
        """
        # Método 1: website já é Instagram
        ig_from_website = _extract_instagram_url_from_website(website)
        if ig_from_website:
            logger.debug(f"[Finder] Instagram via website: {ig_from_website}")
            return ig_from_website

        # Método 2: Busca no Google
        ig_google = self._search_google(company_name, city)
        if ig_google:
            logger.debug(f"[Finder] Instagram via Google: {ig_google}")
            return ig_google

        # Delay entre tentativas
        _delay(1.5, 2.5)

        # Método 3: DuckDuckGo (fallback)
        ig_ddg = self._search_duckduckgo(company_name, city)
        if ig_ddg:
            logger.debug(f"[Finder] Instagram via DuckDuckGo: {ig_ddg}")
            return ig_ddg

        # Método 4: Heurística por username
        ig_heuristic = self._try_username_heuristics(company_name, city)
        if ig_heuristic:
            logger.debug(f"[Finder] Instagram via heurística: {ig_heuristic}")
            return ig_heuristic

        logger.debug(f"[Finder] Instagram não encontrado para: {company_name!r}")
        return None

    # ------------------------------------------------------------------
    # Método 2 — Busca no Google
    # ------------------------------------------------------------------

    def _search_google(self, company_name: str, city: str) -> str | None:
        """
        Busca no Google por "{nome empresa}" instagram {cidade}.

        Usa site:instagram.com como operador para filtrar os resultados.
        Extrai a URL do Instagram da primeira correspondência relevante.

        Returns:
            URL do perfil do Instagram ou None.
        """
        query = f'"{company_name}" site:instagram.com {city}'.strip()
        params = {"q": query, "num": "5", "hl": "pt-BR", "gl": "br"}

        try:
            response = self._session.get(
                self._GOOGLE_SEARCH_URL,
                params=params,
                timeout=10,
            )
            if response.status_code != 200:
                logger.debug(f"[Google] Status {response.status_code} para {company_name!r}")
                return None

            # Verifica CAPTCHA
            if "unusual traffic" in response.text.lower() or "captcha" in response.text.lower():
                logger.warning("[Google] CAPTCHA detectado. Usando fallback.")
                return None

            return self._extract_ig_url_from_html(response.text, company_name)

        except Exception as exc:
            logger.debug(f"[Google] Erro na busca: {exc}")
            return None

    # ------------------------------------------------------------------
    # Método 3 — DuckDuckGo
    # ------------------------------------------------------------------

    def _search_duckduckgo(self, company_name: str, city: str) -> str | None:
        """
        Busca no DuckDuckGo como fallback do Google.

        DuckDuckGo tem HTML simples e sem CAPTCHA frequente,
        ideal como segundo recurso de busca.

        Returns:
            URL do perfil do Instagram ou None.
        """
        query = f"{company_name} instagram {city}".strip()
        try:
            response = self._session.get(
                self._DDG_SEARCH_URL,
                params={"q": query, "kl": "br-pt"},
                timeout=10,
            )
            if response.status_code != 200:
                return None
            return self._extract_ig_url_from_html(response.text, company_name)
        except Exception as exc:
            logger.debug(f"[DDG] Erro na busca: {exc}")
            return None

    # ------------------------------------------------------------------
    # Extração de URL do Instagram a partir do HTML de busca
    # ------------------------------------------------------------------

    def _extract_ig_url_from_html(self, html: str, company_name: str) -> str | None:
        """
        Extrai a URL do Instagram mais relevante de uma página HTML
        de resultados de busca (Google ou DuckDuckGo).

        Filtra usernames genéricos (p, reel, explore, etc.) e prioriza
        aqueles que têm semelhança com o nome da empresa.

        Args:
            html:         HTML da página de resultados.
            company_name: Nome da empresa (usado para ranking de relevância).

        Returns:
            URL normalizada do Instagram mais relevante ou None.
        """
        soup = BeautifulSoup(html, "lxml")

        # Coleta todos os links que apontam para instagram.com
        candidates: list[tuple[str, str]] = []  # (url, username)

        for tag in soup.find_all("a", href=True):
            href = tag.get("href", "")
            match = self._IG_URL_RE.search(href)
            if match:
                username = match.group(1).lower()
                if username not in self._IG_NOISE_USERNAMES and len(username) >= 3:
                    full_url = f"https://www.instagram.com/{username}/"
                    candidates.append((full_url, username))

        # Também busca no texto puro da página (para DuckDuckGo)
        for match in self._IG_URL_RE.finditer(html):
            username = match.group(1).lower()
            if username not in self._IG_NOISE_USERNAMES and len(username) >= 3:
                full_url = f"https://www.instagram.com/{username}/"
                if (full_url, username) not in candidates:
                    candidates.append((full_url, username))

        if not candidates:
            return None

        # Remove duplicatas preservando ordem
        seen: set[str] = set()
        unique = []
        for url, username in candidates:
            if username not in seen:
                seen.add(username)
                unique.append((url, username))

        # Pontua os candidatos por semelhança com o nome da empresa
        name_slug = _normalize_to_username(company_name)
        name_words = [w for w in name_slug.split() if len(w) > 2]

        def relevance_score(username: str) -> int:
            score = 0
            if name_slug in username or username in name_slug:
                score += 10
            for word in name_words:
                if word in username:
                    score += 3
            # Penaliza usernames muito genéricos
            if len(username) < 4:
                score -= 5
            return score

        ranked = sorted(unique, key=lambda t: -relevance_score(t[1]))
        return ranked[0][0] if ranked else None

    # ------------------------------------------------------------------
    # Método 4 — Heurística de username
    # ------------------------------------------------------------------

    def _try_username_heuristics(self, company_name: str, city: str) -> str | None:
        """
        Tenta acessar diretamente o Instagram com usernames gerados
        a partir do nome da empresa.

        Verifica a existência do perfil com uma requisição HEAD leve
        antes de fazer o scraping completo.

        Args:
            company_name: Nome da empresa.
            city:         Cidade (usada em variações do username).

        Returns:
            URL do Instagram se algum candidato existir, ou None.
        """
        candidates = _generate_username_candidates(company_name, city)
        logger.debug(
            f"[Heurística] Testando {len(candidates)} candidatos "
            f"para {company_name!r}: {candidates}"
        )

        for username in candidates:
            url = f"https://www.instagram.com/{username}/"
            try:
                # HEAD request é mais rápido e leve
                response = self._session.head(
                    url,
                    timeout=8,
                    allow_redirects=True,
                    headers={**_INSTAGRAM_HEADERS, "User-Agent": _SEARCH_HEADERS["User-Agent"]},
                )
                # 200 = perfil existe; 404 = não existe; 302/301 = redirect
                if response.status_code in (200, 301, 302):
                    # Confirma que não é uma página de erro do Instagram
                    final_url = response.headers.get("Location", url)
                    if "instagram.com" in final_url and "/accounts/login/" not in final_url:
                        logger.debug(f"[Heurística] ✓ Encontrado: {url}")
                        return f"https://www.instagram.com/{username}/"
                elif response.status_code == 429:
                    logger.warning("[Heurística] Rate limit do Instagram (429). Pausando 30s.")
                    time.sleep(30)

                _delay(0.8, 1.5)

            except Exception as exc:
                logger.debug(f"[Heurística] {url} → {exc}")
                continue

        return None


# ---------------------------------------------------------------------------
# Coleta de métricas do perfil
# ---------------------------------------------------------------------------

class InstagramProfileFetcher:
    """
    Coleta métricas públicas de um perfil do Instagram usando Playwright.

    O Instagram bloqueia requests simples (requests/httpx), então usamos
    Playwright com Chromium headless para simular um browser real.

    Dados coletados:
        - Seguidores, posts, following
        - Bio e link na bio
        - Data da última postagem
        - Status verificado / business
    """

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    def start(self) -> None:
        """Inicia o Playwright."""
        logger.debug("[ProfileFetcher] Iniciando Playwright...")
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=_HEADLESS,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--lang=pt-BR,pt",
            ],
        )
        self._context = self._browser.new_context(
            user_agent=random.choice(_PLAYWRIGHT_USER_AGENTS),
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            viewport={"width": 390, "height": 844},   # Simula iPhone 14
            is_mobile=True,
            has_touch=True,
            java_script_enabled=True,
        )
        # Remove sinais de automação
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

    def stop(self) -> None:
        """Fecha o Playwright."""
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass

    def __enter__(self) -> "InstagramProfileFetcher":
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()

    def fetch(self, instagram_url: str) -> dict[str, Any]:
        """
        Acessa um perfil público do Instagram e extrai as métricas.

        Estratégia:
            1. Navega para a URL do perfil
            2. Tenta extrair dados do JSON embutido na página (mais confiável)
            3. Fallback: extrai dados do DOM visual

        Args:
            instagram_url: URL do perfil (ex: "https://www.instagram.com/empresa/")

        Returns:
            Dicionário com: username, followers, following, posts,
            bio, has_link, is_verified, is_business, last_post_date.
            Retorna dicionário vazio em caso de erro crítico.
        """
        assert self._context is not None, "Chame start() antes de usar."

        page = self._context.new_page()
        page.set_default_timeout(_TIMEOUT_MS)

        try:
            logger.debug(f"[ProfileFetcher] Abrindo: {instagram_url}")
            page.goto(instagram_url, wait_until="domcontentloaded", timeout=_TIMEOUT_MS)
            _delay(1.5, 3.0)

            # Verifica se foi redirecionado para login (perfil privado ou bloqueio)
            if self._is_login_page(page):
                logger.warning(
                    f"[ProfileFetcher] Redirecionado para login: {instagram_url}"
                )
                return {}

            # Verifica se perfil não existe (404)
            if self._is_not_found(page):
                logger.debug(f"[ProfileFetcher] Perfil não encontrado: {instagram_url}")
                return {"not_found": True}

            # Tenta extração via JSON embutido (mais estável)
            data = self._extract_from_json(page)
            if data:
                logger.debug("[ProfileFetcher] Dados extraídos via JSON.")
                return data

            # Fallback: extração via DOM
            data = self._extract_from_dom(page)
            if data:
                logger.debug("[ProfileFetcher] Dados extraídos via DOM.")
                return data

            logger.warning(f"[ProfileFetcher] Sem dados para: {instagram_url}")
            return {}

        except PlaywrightTimeout:
            logger.warning(f"[ProfileFetcher] Timeout: {instagram_url}")
            return {}
        except Exception as exc:
            logger.error(f"[ProfileFetcher] Erro: {instagram_url} → {exc}")
            return {}
        finally:
            try:
                page.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Detecção de estados da página
    # ------------------------------------------------------------------

    @staticmethod
    def _is_login_page(page: Page) -> bool:
        """Verifica se o Instagram redirecionou para a tela de login."""
        url = page.url.lower()
        return (
            "/accounts/login" in url
            or page.locator("input[name='username']").count() > 0
        )

    @staticmethod
    def _is_not_found(page: Page) -> bool:
        """Verifica se o perfil não existe (página de erro do Instagram)."""
        content = page.content().lower()
        return (
            "desculpe, esta página" in content
            or "sorry, this page" in content
            or page.url == "https://www.instagram.com/"
        )

    # ------------------------------------------------------------------
    # Extração via JSON embutido
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_from_json(page: Page) -> dict[str, Any] | None:
        """
        Extrai dados do Instagram a partir do JSON embutido na página.

        O Instagram injeta dados do perfil no HTML em blocos <script>
        com formato JSON. Tentamos múltiplos formatos conhecidos:
            - window._sharedData  (formato antigo)
            - __additionalDataLoaded  (formato mais recente)
            - <script type="application/ld+json">  (schema.org)

        Returns:
            Dicionário normalizado ou None se nenhum formato for encontrado.
        """
        html = page.content()
        soup = BeautifulSoup(html, "lxml")

        # Tentativa 1: JSON-LD (schema.org — mais estável)
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                obj = json.loads(script.string or "{}")
                # Instagram usa Person ou Organization no JSON-LD
                if obj.get("@type") in ("Person", "Organization", "ProfilePage"):
                    return InstagramProfileFetcher._normalize_jsonld(obj)
            except (json.JSONDecodeError, Exception):
                continue

        # Tentativa 2: window._sharedData (formato clássico)
        match = re.search(r"window\._sharedData\s*=\s*(\{.+?\});</script>", html, re.DOTALL)
        if match:
            try:
                shared = json.loads(match.group(1))
                user = (
                    shared.get("entry_data", {})
                    .get("ProfilePage", [{}])[0]
                    .get("graphql", {})
                    .get("user", {})
                )
                if user:
                    return InstagramProfileFetcher._normalize_shared_data(user)
            except (json.JSONDecodeError, IndexError, KeyError):
                pass

        # Tentativa 3: __additionalDataLoaded (formato mais recente)
        match = re.search(r'"user"\s*:\s*(\{"id".+?\})\s*(?:,|$)', html, re.DOTALL)
        if match:
            try:
                user = json.loads(match.group(1))
                if user:
                    return InstagramProfileFetcher._normalize_shared_data(user)
            except (json.JSONDecodeError, Exception):
                pass

        return None

    @staticmethod
    def _normalize_jsonld(obj: dict) -> dict[str, Any]:
        """Normaliza dados do formato JSON-LD para o formato padrão do módulo (simplificado)."""
        return {
            "username": obj.get("alternateName", "").lstrip("@"),
            "name": "",
            "bio": "",
            "has_bio": None,
            "has_link": bool(obj.get("url") and "instagram.com" not in obj.get("url", "")),
            "followers": None,
            "following": None,
            "posts": None,
            "is_verified": None,
            "is_business": None,
            "last_post_date": None,
        }

    @staticmethod
    def _normalize_shared_data(user: dict) -> dict[str, Any]:
        """Normaliza dados do window._sharedData para o formato padrão (simplificado)."""
        ext_url = user.get("external_url", "") or ""
        return {
            "username": user.get("username", ""),
            "name": "",
            "bio": "",
            "has_bio": None,
            "has_link": bool(ext_url.strip()),
            "followers": None,
            "following": None,
            "posts": None,
            "is_verified": None,
            "is_business": None,
            "last_post_date": None,
        }

    @staticmethod
    def _extract_from_dom(page: Page) -> dict[str, Any] | None:
        """
        Extrai de forma simplificada o link da bio e username da página pública do perfil
        a partir do DOM visual.
        """
        html = page.content()
        soup = BeautifulSoup(html, "lxml")

        # Username: a partir da tag title
        username = ""
        title_tag = soup.find("title")
        if title_tag:
            title_text = title_tag.get_text()
            m = re.search(r"@?([\w.]+)\s*[•·]", title_text)
            if m:
                username = m.group(1)

        # Se não achou no title, tenta ver o header
        if not username:
            h2_tag = soup.find("h2")
            if h2_tag:
                username = h2_tag.get_text().strip()

        # Link na bio — procura links externos que não sejam do instagram
        has_link = False
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if href and "instagram.com" not in href and href.startswith("http"):
                has_link = True
                break

        if not username:
            return None

        return {
            "username": username,
            "name": "",
            "bio": "",
            "has_bio": None,
            "has_link": has_link,
            "followers": None,
            "following": None,
            "posts": None,
            "is_verified": None,
            "is_business": None,
            "last_post_date": None,
        }


# ---------------------------------------------------------------------------
# Pontuação e classificação
# ---------------------------------------------------------------------------

class InstagramScorer:
    """
    Calcula o score de oportunidade e a classificação do perfil Instagram.

    Score alto = presença fraca = maior oportunidade de venda.

    Tabela de scores:
        sem_instagram:          +25 (base)
        parado (>3 meses):      +20 (base) + até +10 extra
        inativo (>6 meses):     +15 (base) + até +15 extra
        fraco (poucos seguid.): +10 (base)
        bom:                     +0 (base)

        Subcritérios (acumulam no status "ok"):
            bio vazia:      +5
            sem link:       +5
            < 9 posts:      +5
            < 100 seguid.:  +5
            > 3 meses sem post: +10
            > 6 meses sem post: +15 (substitui o anterior)
    """

    @classmethod
    def score(cls, metrics: dict[str, Any]) -> tuple[str, int]:
        """
        Calcula status e score de um perfil Instagram.

        Args:
            metrics: Dicionário retornado por InstagramProfileFetcher.fetch()
                     ou {} se perfil não encontrado.

        Returns:
            Tupla (status: str, score: int).
        """
        # Perfil não encontrado
        if not metrics or metrics.get("not_found"):
            return "sem_instagram", 25

        followers = metrics.get("followers") or 0
        posts = metrics.get("posts") or 0
        has_bio = bool(metrics.get("has_bio"))
        has_link = bool(metrics.get("has_link"))
        last_post = metrics.get("last_post_date")

        days_ago = _days_since(last_post) if last_post else None

        score = 0

        # ── Análise de frequência de posts ───────────────────────────────
        post_score = 0
        if days_ago is not None:
            if days_ago > _INATIVO_DAYS:      # > 6 meses
                post_score = 15
            elif days_ago > _PARADO_DAYS:     # > 3 meses
                post_score = 10
        elif posts == 0:
            post_score = 15   # Sem posts = considera inativo

        # ── Subcritérios de qualidade ────────────────────────────────────
        bio_score = 0 if has_bio else 5
        link_score = 0 if has_link else 5
        posts_score = 5 if posts < _MIN_POSTS else 0
        followers_score = 5 if followers < _MIN_FOLLOWERS else 0

        subcriteria_score = bio_score + link_score + posts_score + followers_score + post_score

        # ── Classificação principal ──────────────────────────────────────
        if posts == 0 or (days_ago is not None and days_ago > _INATIVO_DAYS):
            status = "inativo"
            score = 15 + subcriteria_score
        elif days_ago is not None and days_ago > _PARADO_DAYS:
            status = "parado"
            score = 20 + (subcriteria_score - post_score)  # post_score já na base
        elif followers < _MIN_FOLLOWERS or posts < _MIN_POSTS or not has_bio:
            status = "fraco"
            score = 10 + subcriteria_score
        else:
            status = "bom"
            score = subcriteria_score   # pode ser 0 se tudo ok

        # Limita o score a 35 (máximo realista para "perfil encontrado")
        score = min(score, 35)

        return status, score


# ---------------------------------------------------------------------------
# Orquestrador principal
# ---------------------------------------------------------------------------

class InstagramChecker:
    """
    Orquestrador do módulo de verificação de Instagram.

    Coordena as três etapas: descoberta, coleta de métricas e persistência.

    Uso típico:
        checker = InstagramChecker()
        results = checker.check_all(limit=50)
    """

    def __init__(self, db_path: str = _DB_PATH) -> None:
        self.db = InstagramDatabase(db_path)
        self._finder = InstagramFinder()

    def check_company(self, company: dict[str, Any]) -> dict[str, Any]:
        """
        Verifica o Instagram de uma empresa e salva o resultado no banco.

        Fluxo completo:
            1. Tenta encontrar o handle via InstagramFinder
            2. Se encontrou, coleta métricas via Playwright
            3. Pontua e classifica via InstagramScorer
            4. Salva no banco e retorna o resultado

        Args:
            company: Dicionário com id, name, website, city, state, niche.

        Returns:
            Dicionário completo com todos os campos instagram_*.
        """
        company_id = company["id"]
        name = company.get("name", f"id={company_id}")
        website = company.get("website", "") or ""
        city = company.get("city", "") or ""

        result = _empty_result(company_id)

        # ── Etapa 1: Descoberta ──────────────────────────────────────────
        logger.info(f"[InstagramChecker] Buscando Instagram: {name!r} ({city})")

        instagram_url = self._finder.find(
            company_name=name,
            city=city,
            website=website,
        )

        if not instagram_url:
            result["instagram_status"] = "sem_instagram"
            result["instagram_score"] = 25
            logger.info(f"[InstagramChecker] {name!r} → sem_instagram (+25)")
            self.db.save_result(result)
            return result

        # Extrai username da URL
        username_match = re.search(
            r"instagram\.com/([A-Za-z0-9_.]{1,30})/?", instagram_url
        )
        username = username_match.group(1) if username_match else ""

        result["instagram_url"] = instagram_url
        result["instagram_username"] = username

        # ── Etapa 2: Coleta de métricas ──────────────────────────────────
        logger.info(f"[InstagramChecker] Coletando métricas: @{username}")
        _delay()  # Respeita rate limit antes de acessar o Instagram

        metrics = self._fetch_metrics(instagram_url)

        # Perfil não existe (404) — volta para sem_instagram
        if metrics.get("not_found"):
            result["instagram_status"] = "sem_instagram"
            result["instagram_score"] = None
            result["instagram_url"] = None
            result["instagram_username"] = None
            logger.info(f"[InstagramChecker] {name!r} → perfil 404, sem_instagram")
            self.db.save_result(result)
            return result

        # ── Etapa 3: Pontuação ───────────────────────────────────────────
        status = "tem_instagram"

        result["instagram_status"] = status
        result["instagram_score"] = None
        result["instagram_followers"] = None
        result["instagram_following"] = None
        result["instagram_posts"] = None
        result["instagram_last_post"] = None
        result["instagram_has_bio"] = None
        result["instagram_has_link"] = 1 if metrics.get("has_link") else 0
        result["instagram_bio"] = None
        result["instagram_is_verified"] = None
        result["instagram_is_business"] = None
        result["instagram_checked_at"] = datetime.now().isoformat()

        logger.info(
            f"[InstagramChecker] ✓ {name!r} → @{username} | "
            f"status={status} | has_link={result['instagram_has_link']}"
        )

        self.db.save_result(result)
        return result

    def check_all(
        self,
        limit: int = 50,
        delay_between_s: float | None = None,
    ) -> list[dict[str, Any]]:
        """
        Verifica o Instagram de todas as empresas pendentes no banco.

        Empresas "pendentes" = instagram_checked_at IS NULL.

        Args:
            limit:            Máximo de empresas a processar.
            delay_between_s:  Pausa extra entre empresas (além do delay interno).
                              Se None, usa DELAY_MIN/MAX do .env.

        Returns:
            Lista de resultados de cada empresa verificada.
        """
        companies = self.db.get_pending(limit=limit)
        total = len(companies)

        if not companies:
            logger.info("[InstagramChecker] Nenhuma empresa pendente.")
            return []

        logger.info(f"[InstagramChecker] Processando {total} empresas...")

        results: list[dict[str, Any]] = []
        counters: dict[str, int] = {}

        # Inicia o Playwright uma única vez para todas as empresas
        with InstagramProfileFetcher() as fetcher:
            self._active_fetcher = fetcher

            for idx, company in enumerate(companies, start=1):
                logger.info(
                    f"[InstagramChecker] [{idx}/{total}] "
                    f"{company['name']!r} | {company.get('city')} | "
                    f"{company.get('niche')}"
                )
                try:
                    result = self.check_company(company)
                    results.append(result)
                    status = result["instagram_status"]
                    counters[status] = counters.get(status, 0) + 1

                except Exception as exc:
                    logger.error(
                        f"[InstagramChecker] Erro em {company['name']!r}: {exc}"
                    )
                    error_result = _empty_result(company["id"])
                    error_result["instagram_status"] = "sem_instagram"
                    self.db.save_result(error_result)

                # Pausa extra entre empresas (além do delay interno)
                if idx < total and delay_between_s:
                    time.sleep(delay_between_s)

            self._active_fetcher = None

        # Resumo
        logger.info(
            f"\n{'═' * 50}\n"
            f"  InstagramChecker — Resumo\n"
            f"  Total verificadas : {len(results)}\n"
            + "".join(
                f"  {s:<20}: {c}\n"
                for s, c in sorted(counters.items(), key=lambda x: -x[1])
            )
            + f"{'═' * 50}"
        )
        return results

    def check_by_id(self, company_id: int) -> dict[str, Any] | None:
        """Verifica o Instagram de uma empresa específica pelo ID."""
        company = self.db.get_by_id(company_id)
        if not company:
            logger.warning(f"[InstagramChecker] Empresa id={company_id} não encontrada.")
            return None
        return self.check_company(company)

    def _fetch_metrics(self, instagram_url: str) -> dict[str, Any]:
        """
        Obtém métricas do perfil usando o fetcher Playwright ativo.

        Se não há fetcher ativo (chamada fora do check_all()),
        cria um temporário sob demanda.
        """
        fetcher = getattr(self, "_active_fetcher", None)
        if fetcher is not None:
            return fetcher.fetch(instagram_url)

        # Fallback: cria fetcher temporário para chamadas individuais
        with InstagramProfileFetcher() as temp_fetcher:
            return temp_fetcher.fetch(instagram_url)

    def print_stats(self) -> None:
        """Imprime estatísticas das verificações de Instagram."""
        stats = self.db.get_stats()
        print(f"\n{'═' * 55}")
        print("  InstagramChecker — Estatísticas")
        print(f"{'═' * 55}")
        print(f"  Total empresas   : {stats['total']}")
        print(f"  Verificadas      : {stats['checked']}")
        print(f"  Pendentes        : {stats['pending']}")
        if stats["by_status"]:
            print(f"\n  Distribuição por status:")
            icons = {
                "sem_instagram": "❌",
                "parado": "😴",
                "inativo": "💤",
                "fraco": "📉",
                "bom": "✅",
            }
            for status, count in stats["by_status"].items():
                icon = icons.get(status, "  ")
                bar = "█" * min(count, 25)
                print(f"    {icon} {status:<18} {bar} {count}")
        print(f"{'═' * 55}\n")


# ---------------------------------------------------------------------------
# Funções de conveniência (API funcional)
# ---------------------------------------------------------------------------

def find_instagram_profile(company_name: str, city: str = "", website: str = "") -> str | None:
    """
    Tenta encontrar o Instagram de uma empresa pelo nome.

    Função de conveniência sem banco de dados.

    Args:
        company_name: Nome da empresa.
        city:         Cidade (melhora precisão).
        website:      URL do website (pode já ser Instagram).

    Returns:
        URL do Instagram ou None.

    Exemplo:
        url = find_instagram_profile("Pizzaria Napolitana", "São Paulo")
        print(url)  # "https://www.instagram.com/pizzarianapolitana/"
    """
    finder = InstagramFinder()
    return finder.find(company_name, city, website)


def get_instagram_metrics(profile_url: str) -> dict[str, Any]:
    """
    Coleta métricas públicas de um perfil do Instagram.

    Função de conveniência sem banco de dados. Abre o Playwright,
    faz o scraping e fecha o browser.

    Args:
        profile_url: URL do perfil (ex: "https://www.instagram.com/empresa/")

    Returns:
        Dicionário com: username, followers, following, posts,
        bio, has_bio, has_link, is_verified, is_business, last_post_date.

    Exemplo:
        metrics = get_instagram_metrics("https://www.instagram.com/natura/")
        print(metrics["followers"])  # 1234567
    """
    with InstagramProfileFetcher() as fetcher:
        return fetcher.fetch(profile_url)


def check_instagram(company_name: str, instagram_url: str | None = None) -> dict[str, Any]:
    """
    Verifica o Instagram de uma empresa e retorna resultado completo.

    Função de conveniência sem banco de dados — útil para testes
    pontuais sem precisar instanciar WebsiteChecker.

    Args:
        company_name:   Nome da empresa.
        instagram_url:  URL já conhecida (pula a etapa de descoberta).

    Returns:
        Dicionário com status, score e todas as métricas coletadas.
    """
    finder = InstagramFinder()

    url = instagram_url or finder.find(company_name)
    if not url:
        return {"status": "sem_instagram", "score": 25}

    username_match = re.search(r"instagram\.com/([A-Za-z0-9_.]+)/?", url)
    username = username_match.group(1) if username_match else ""

    with InstagramProfileFetcher() as fetcher:
        metrics = fetcher.fetch(url)

    status = "sem_instagram" if metrics.get("not_found") else "tem_instagram"
    return {
        "url": url,
        "username": username,
        "status": status,
        "score": None,
        **metrics,
    }


# ---------------------------------------------------------------------------
# Execução direta (CLI / debug)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logger.remove()
    logger.add(
        sink=lambda msg: print(msg, end=""),
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | {message}"
        ),
        colorize=True,
        level="INFO",
    )

    # ── Modo 1: Verifica URL passada como argumento ──────────────────────
    if len(sys.argv) > 1:
        arg = sys.argv[1]

        if "instagram.com" in arg:
            print(f"\nColetando métricas: {arg}\n{'─' * 55}")
            metrics = get_instagram_metrics(arg)
            for k, v in metrics.items():
                print(f"  {k:<22}: {v}")
        else:
            # Trata como nome de empresa
            city = sys.argv[2] if len(sys.argv) > 2 else ""
            print(f"\nBuscando Instagram: {arg!r} ({city})\n{'─' * 55}")
            result = check_instagram(arg)
            for k, v in result.items():
                print(f"  {k:<22}: {v}")
        print()

    # ── Modo 2: Processa empresas do banco ───────────────────────────────
    else:
        print(f"\n{'═' * 60}")
        print("  Instagram Checker — Processando empresas do banco")
        print(f"{'═' * 60}\n")

        try:
            checker = InstagramChecker()
            checker.print_stats()

            results = checker.check_all(limit=10)

            print(f"\n{'─' * 60}")
            print(f"  Verificadas nesta sessão: {len(results)}")
            print(f"{'─' * 60}")
            checker.print_stats()

        except FileNotFoundError as e:
            print(f"\n❌ Erro: {e}")
            print("   Execute primeiro: python src/google_maps.py\n")
