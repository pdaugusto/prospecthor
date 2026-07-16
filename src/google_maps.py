"""
google_maps.py — Módulo de busca de empresas no Google Maps
============================================================

Realiza buscas no Google Maps por nicho e região, coletando dados
detalhados de cada estabelecimento encontrado.

Fluxo principal:
    1. Recebe nicho, cidade, estado e limite de resultados
    2. Tenta via Google Places API (se GOOGLE_MAPS_API_KEY estiver configurada)
    3. Fallback: scraping com Playwright (Chromium headless)
    4. Salva cada empresa no banco SQLite (data/leads.db)
    5. Retorna lista de dicionários com os dados coletados

Uso:
    from src.google_maps import GoogleMapsSearcher

    searcher = GoogleMapsSearcher()
    companies = searcher.search(
        niche="restaurante",
        city="Porto Alegre",
        state="RS",
        max_results=100,
    )
    for company in companies:
        print(company["name"], company["rating"])
"""

from __future__ import annotations

import os
import re
import math
import time
import random
import psycopg2
import psycopg2.extras
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
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
# Configuração inicial
# ---------------------------------------------------------------------------

load_dotenv()

# Variáveis de ambiente
_API_KEY: str = os.getenv("GOOGLE_MAPS_API_KEY", "")
_DATABASE_URL: str = os.getenv("DATABASE_URL", "")
_DB_PATH: str = os.getenv("DATABASE_PATH", "data/leads.db")  # mantido para compat com main.py
_HEADLESS: bool = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
_TIMEOUT_MS: int = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "30000"))
_DELAY_MIN: float = float(os.getenv("REQUEST_DELAY_MIN_S", "2.0"))
_DELAY_MAX: float = float(os.getenv("REQUEST_DELAY_MAX_S", "5.0"))

# Google Places API — endpoints
_PLACES_TEXT_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
_PLACES_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
_PLACES_NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"

# Campos solicitados à Places Details API (reduz custo por requisição)
_PLACES_DETAIL_FIELDS = (
    "place_id,name,formatted_address,formatted_phone_number,website,"
    "rating,user_ratings_total,types,opening_hours,geometry,"
    "business_status,price_level,url"
)

# Seletores do Google Maps (DOM — podem mudar com atualizações do Google)
# Usamos múltiplos seletores de fallback para maior resiliência.
_SEL_RESULTS_FEED = 'div[role="feed"]'
_SEL_RESULT_ITEMS = 'a.hfpxzc, a[href*="maps/place"]'
_SEL_PANEL_NAME = "h1.DUwDvf, h1.fontHeadlineLarge, h1"
_SEL_PANEL_RATING = "div.F7nice span[aria-hidden='true'], span.MW4etd"
_SEL_PANEL_REVIEWS = "span[aria-label*='avaliações'], span[aria-label*='reviews']"
_SEL_PANEL_ADDRESS = "button[data-item-id='address'], [data-tooltip='Copiar endereço']"
_SEL_PANEL_PHONE = "button[data-item-id^='phone'], [data-tooltip='Copiar número de telefone']"
_SEL_PANEL_WEBSITE = "a[data-item-id='authority'], a[aria-label*='Site']"
_SEL_PANEL_CATEGORY = "button.DkEaL, span.DkEaL"
_SEL_PANEL_OPEN_STATUS = "span.ZDu9vd span, [data-item-id='oh'] span"
_SEL_PANEL_HOURS = "table.WgFkxc"

# Strings de detecção de bloqueio pelo Google
_BLOCK_SIGNALS = [
    "unusual traffic",
    "tráfego incomum",
    "nossos sistemas detectaram",
    "captcha",
    "I'm not a robot",
]


# ---------------------------------------------------------------------------
# Schema do banco de dados
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS companies (
    id                  SERIAL PRIMARY KEY,
    place_id            TEXT UNIQUE,
    name                TEXT NOT NULL,
    category            TEXT,
    niche               TEXT,
    city                TEXT,
    state               TEXT,
    address             TEXT,
    phone               TEXT,
    website             TEXT,
    rating              REAL,
    review_count        INTEGER,
    is_open_now         INTEGER,
    opening_hours       TEXT,
    latitude            REAL,
    longitude           REAL,
    maps_url            TEXT,
    business_status     TEXT,
    source              TEXT,
    scraped_at          TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (NOW()::TEXT)
);
"""

_UPSERT_COMPANY_SQL = """
INSERT INTO companies (
    place_id, name, category, niche, city, state, address, phone,
    website, rating, review_count, is_open_now, opening_hours,
    latitude, longitude, maps_url, business_status, source, scraped_at
) VALUES (
    %(place_id)s, %(name)s, %(category)s, %(niche)s, %(city)s, %(state)s, %(address)s, %(phone)s,
    %(website)s, %(rating)s, %(review_count)s, %(is_open_now)s, %(opening_hours)s,
    %(latitude)s, %(longitude)s, %(maps_url)s, %(business_status)s, %(source)s, %(scraped_at)s
)
ON CONFLICT(place_id) DO UPDATE SET
    name            = EXCLUDED.name,
    category        = EXCLUDED.category,
    address         = EXCLUDED.address,
    phone           = EXCLUDED.phone,
    website         = EXCLUDED.website,
    rating          = EXCLUDED.rating,
    review_count    = EXCLUDED.review_count,
    is_open_now     = EXCLUDED.is_open_now,
    opening_hours   = EXCLUDED.opening_hours,
    latitude        = EXCLUDED.latitude,
    longitude       = EXCLUDED.longitude,
    maps_url        = EXCLUDED.maps_url,
    business_status = EXCLUDED.business_status,
    source          = EXCLUDED.source,
    scraped_at      = EXCLUDED.scraped_at
RETURNING id;
"""


# ---------------------------------------------------------------------------
# Utilitários internos
# ---------------------------------------------------------------------------

def _random_delay(min_s: float = _DELAY_MIN, max_s: float = _DELAY_MAX) -> None:
    """Aguarda um intervalo aleatório entre requisições para evitar bloqueios."""
    delay = random.uniform(min_s, max_s)
    logger.debug(f"Aguardando {delay:.1f}s antes da próxima ação...")
    time.sleep(delay)


def _normalize_phone(raw: str) -> str:
    """Remove caracteres não numéricos do telefone, mantendo o + inicial."""
    if not raw:
        return ""
    cleaned = re.sub(r"[^\d+]", "", raw.strip())
    return cleaned


def _parse_review_count(raw: str) -> int:
    """
    Converte strings como '(1.234)', '1,234 reviews', '2.3 mil' em inteiro.

    Exemplos:
        '(1.234)'     → 1234
        '2,3 mil'     → 2300
        '15 reviews'  → 15
        ''            → 0
    """
    if not raw:
        return 0
    raw = raw.lower().replace("(", "").replace(")", "").strip()

    # Padrão brasileiro: "2,3 mil" ou "1,5 mil"
    mil_match = re.search(r"([\d,\.]+)\s*mil", raw)
    if mil_match:
        num_str = mil_match.group(1).replace(".", "").replace(",", ".")
        try:
            return int(float(num_str) * 1000)
        except ValueError:
            pass

    # Extrai todos os dígitos da string
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else 0


def _parse_rating(raw: str) -> float | None:
    """Converte '4,5' ou '4.5' em float. Retorna None se inválido."""
    if not raw:
        return None
    cleaned = raw.strip().replace(",", ".")
    try:
        rating = float(cleaned)
        if 0.0 <= rating <= 5.0:
            return round(rating, 1)
    except ValueError:
        pass
    return None


_SOCIAL_SITE_MARKERS = (
    "instagram.com", "facebook.com", "fb.com", "linktr.ee",
    "bio.link", "wa.me", "whatsapp.com", "tiktok.com",
)


def _has_own_website(url: str | None) -> bool:
    """True se a URL é site próprio (não só rede social / vazio)."""
    w = (url or "").strip().lower()
    if not w:
        return False
    return not any(m in w for m in _SOCIAL_SITE_MARKERS)


def _extract_place_id_from_url(url: str) -> str | None:
    """
    Extrai o place_id ou constrói um ID único a partir da URL do Google Maps.

    Exemplos de URLs:
        https://www.google.com/maps/place/Nome+Empresa/@lat,lon,17z/data=...!1s<PLACE_ID>!...
        https://maps.google.com/maps?cid=12345678901234567
    """
    # Tenta extrair o CID (Content ID)
    cid_match = re.search(r"[?&]cid=(\d+)", url)
    if cid_match:
        return f"cid:{cid_match.group(1)}"

    # Tenta extrair o Place ID do segmento de dados da URL (formato !1sChIJ...)
    place_match = re.search(r"!1s(ChIJ[^!&]+)", url)
    if place_match:
        return urllib.parse.unquote(place_match.group(1))

    # Fallback: usa a parte do path após /place/ como identificador
    path_match = re.search(r"/place/([^/@]+)", url)
    if path_match:
        return f"slug:{urllib.parse.unquote(path_match.group(1))}"

    return None


def _extract_coordinates_from_url(url: str) -> tuple[float | None, float | None]:
    """
    Extrai latitude e longitude da URL do Google Maps.

    Exemplo: .../@-30.0346,-51.2177,17z/...  →  (-30.0346, -51.2177)
    """
    coord_match = re.search(r"/@(-?\d+\.\d+),(-?\d+\.\d+)", url)
    if coord_match:
        try:
            lat = float(coord_match.group(1))
            lon = float(coord_match.group(2))
            # Validação básica de faixas geográficas
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon
        except ValueError:
            pass
    return None, None


def _build_empty_company(niche: str, city: str, state: str) -> dict[str, Any]:
    """Retorna um dicionário com todos os campos zerados/None para um lead."""
    return {
        "place_id": None,
        "name": "",
        "category": "",
        "niche": niche,
        "city": city,
        "state": state,
        "address": "",
        "phone": "",
        "website": "",
        "rating": None,
        "review_count": 0,
        "is_open_now": None,
        "opening_hours": "",
        "latitude": None,
        "longitude": None,
        "maps_url": "",
        "business_status": "OPERATIONAL",
        "source": "playwright",
        "scraped_at": datetime.now().isoformat(),
    }


def _is_blocked(page: Page) -> bool:
    """Verifica se o Google exibiu uma página de bloqueio/CAPTCHA."""
    try:
        content = page.content().lower()
        return any(signal in content for signal in _BLOCK_SIGNALS)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------

class Database:
    """
    Gerencia a conexão e operações no banco de dados PostgreSQL via psycopg2.
    Usa a variável de ambiente DATABASE_URL para a conexão.
    """

    def __init__(self, db_path: str = _DB_PATH) -> None:
        # db_path mantido na assinatura para compatibilidade com chamadas existentes
        if not _DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL não configurada no .env. "
                "Defina a string de conexão PostgreSQL (ex: Supabase)."
            )
        self._init_schema()

    def _connect(self):
        """Retorna uma nova conexão psycopg2 ao PostgreSQL."""
        return psycopg2.connect(_DATABASE_URL)

    def _init_schema(self) -> None:
        """Cria as tabelas se ainda não existirem."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(_CREATE_TABLE_SQL)
            conn.commit()
            cur.close()
        finally:
            conn.close()
        logger.debug("Banco de dados PostgreSQL inicializado.")

    def upsert_company(self, company: dict[str, Any]) -> int:
        """
        Insere ou atualiza um registro de empresa via UPSERT.
        Retorna o id da linha inserida/atualizada.
        """
        if not company.get("place_id"):
            slug = f"{company.get('name', '')}_{company.get('city', '')}".lower()
            slug = re.sub(r"\s+", "_", slug)
            company["place_id"] = f"synthetic:{slug}"

        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(_UPSERT_COMPANY_SQL, company)
            row = cur.fetchone()
            conn.commit()
            cur.close()
            return row[0] if row else 0
        finally:
            conn.close()

    def place_id_exists(self, place_id: str) -> bool:
        """Verifica se o place_id já existe no banco (evita reprocessamento)."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM companies WHERE place_id = %s LIMIT 1", (place_id,)
            )
            row = cur.fetchone()
            cur.close()
            return row is not None
        finally:
            conn.close()

    def get_company_count(self, niche: str | None = None, city: str | None = None) -> int:
        """Retorna o número de empresas no banco, com filtros opcionais."""
        query = "SELECT COUNT(*) FROM companies WHERE 1=1"
        params: list[str] = []
        if niche:
            query += " AND niche = %s"
            params.append(niche)
        if city:
            query += " AND city = %s"
            params.append(city)
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(query, params)
            count = cur.fetchone()[0]
            cur.close()
            return count
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Google Places API
# ---------------------------------------------------------------------------

class PlacesAPIClient:
    """
    Cliente para a Google Places API (Text Search + Place Details).

    Requer GOOGLE_MAPS_API_KEY configurada no .env.
    Documentação: https://developers.google.com/maps/documentation/places/web-service
    """

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def text_search(
        self,
        query: str,
        max_results: int = 60,
        language: str = "pt-BR",
        region: str = "br",
    ) -> list[dict[str, Any]]:
        """
        Busca lugares via Text Search API e percorre páginas até atingir max_results.

        A API retorna até 20 resultados por página e suporta até 3 páginas
        (60 resultados no total) via next_page_token.

        Args:
            query:       Texto da busca (ex: "restaurante Porto Alegre RS")
            max_results: Número máximo de resultados a retornar
            language:    Código de idioma BCP-47 (padrão: pt-BR)
            region:      Região para relevância dos resultados (padrão: br)

        Returns:
            Lista de dicionários com os dados brutos da API.
        """
        results: list[dict] = []
        params = {
            "query": query,
            "key": self.api_key,
            "language": language,
            "region": region,
        }

        page = 0
        next_page_token: str | None = None

        while len(results) < max_results:
            if next_page_token:
                # A API exige uma pausa de ~2s antes de usar o next_page_token
                time.sleep(2.5)
                params = {"pagetoken": next_page_token, "key": self.api_key}

            try:
                response = self.session.get(
                    _PLACES_TEXT_SEARCH_URL,
                    params=params,
                    timeout=15,
                )
                response.raise_for_status()
                data = response.json()
            except requests.RequestException as exc:
                logger.error(f"[Places API] Erro na requisição (página {page}): {exc}")
                break

            status = data.get("status")
            if status == "REQUEST_DENIED":
                logger.error(
                    "[Places API] Chave de API inválida ou sem permissão para Places API."
                )
                break
            if status == "OVER_QUERY_LIMIT":
                logger.warning("[Places API] Cota diária atingida. Aguardando 60s...")
                time.sleep(60)
                continue
            if status not in ("OK", "ZERO_RESULTS"):
                logger.warning(f"[Places API] Status inesperado: {status}")
                break
            if status == "ZERO_RESULTS":
                logger.info(f"[Places API] Nenhum resultado para: {query!r}")
                break

            batch = data.get("results", [])
            results.extend(batch)
            logger.info(
                f"[Places API] Página {page + 1}: {len(batch)} resultados "
                f"(total: {len(results)})"
            )

            next_page_token = data.get("next_page_token")
            if not next_page_token or len(results) >= max_results:
                break

            page += 1

        return results[:max_results]

    def get_place_details(self, place_id: str, language: str = "pt-BR") -> dict[str, Any]:
        """
        Retorna detalhes completos de um lugar pelo seu Place ID.

        Args:
            place_id: Identificador único do Google Places (ex: "ChIJ...")
            language: Idioma para os resultados

        Returns:
            Dicionário com os detalhes do lugar (ou {} em caso de erro).
        """
        try:
            response = self.session.get(
                _PLACES_DETAILS_URL,
                params={
                    "place_id": place_id,
                    "fields": _PLACES_DETAIL_FIELDS,
                    "key": self.api_key,
                    "language": language,
                },
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") == "OK":
                return data.get("result", {})
        except requests.RequestException as exc:
            logger.error(f"[Places API] Erro ao buscar detalhes de {place_id}: {exc}")
        return {}

    def normalize_place(
        self,
        raw: dict[str, Any],
        niche: str,
        city: str,
        state: str,
    ) -> dict[str, Any]:
        """
        Normaliza um resultado bruto da Places API para o formato padrão do projeto.

        Args:
            raw:   Resultado bruto da API (pode ser de text_search ou get_place_details)
            niche: Nicho da busca
            city:  Cidade da busca
            state: Estado da busca

        Returns:
            Dicionário no formato padrão do módulo.
        """
        company = _build_empty_company(niche, city, state)

        company["place_id"] = raw.get("place_id", "")
        company["name"] = raw.get("name", "")
        company["address"] = raw.get(
            "formatted_address", raw.get("vicinity", "")
        )
        company["phone"] = _normalize_phone(
            raw.get("formatted_phone_number", raw.get("international_phone_number", ""))
        )
        company["website"] = raw.get("website", "")
        company["rating"] = raw.get("rating")
        company["review_count"] = raw.get("user_ratings_total", 0)
        company["maps_url"] = raw.get(
            "url",
            f"https://www.google.com/maps/place/?q=place_id:{raw.get('place_id', '')}",
        )
        company["business_status"] = raw.get("business_status", "OPERATIONAL")
        company["source"] = "places_api"

        # Categoria: primeiro tipo da lista de types
        types = raw.get("types", [])
        company["category"] = types[0].replace("_", " ").title() if types else ""

        # Coordenadas
        location = raw.get("geometry", {}).get("location", {})
        company["latitude"] = location.get("lat")
        company["longitude"] = location.get("lng")

        # Horários de funcionamento
        oh = raw.get("opening_hours", {})
        company["is_open_now"] = (
            1 if oh.get("open_now") is True
            else 0 if oh.get("open_now") is False
            else None
        )
        weekday_text = oh.get("weekday_text", [])
        company["opening_hours"] = " | ".join(weekday_text) if weekday_text else ""

        return company


# ---------------------------------------------------------------------------
# Playwright Scraper
# ---------------------------------------------------------------------------

class PlaywrightScraper:
    """
    Scraper do Google Maps usando Playwright (Chromium headless).

    Navega pelas páginas de resultados, faz scroll para carregar mais
    empresas e extrai dados dos painéis de detalhes de cada uma.
    """

    # User-Agents reais para rotação (evita detecção trivial de bots)
    _USER_AGENTS = [
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
    ]

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    # ------------------------------------------------------------------
    # Ciclo de vida do browser
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Inicia o Playwright e abre o navegador Chromium."""
        logger.info("[Playwright] Iniciando navegador Chromium...")
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=_HEADLESS,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--lang=pt-BR,pt",
            ],
        )
        self._context = self._browser.new_context(
            user_agent=random.choice(self._USER_AGENTS),
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            viewport={"width": 1366, "height": 768},
            # Mascara sinais de automação
            java_script_enabled=True,
        )
        # Injeta script para mascarar navigator.webdriver
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        logger.info("[Playwright] Navegador pronto.")

    def stop(self) -> None:
        """Fecha o navegador e libera recursos."""
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception as exc:
            logger.warning(f"[Playwright] Erro ao fechar o navegador: {exc}")
        logger.debug("[Playwright] Navegador encerrado.")

    def __enter__(self) -> "PlaywrightScraper":
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Página nova
    # ------------------------------------------------------------------

    def _new_page(self) -> Page:
        """Cria uma nova aba no contexto atual."""
        assert self._context is not None, "Chame start() antes de usar o scraper."
        page = self._context.new_page()
        page.set_default_timeout(_TIMEOUT_MS)
        return page

    # ------------------------------------------------------------------
    # Scraping principal
    # ------------------------------------------------------------------

    def scrape(
        self,
        query: str,
        niche: str,
        city: str,
        state: str,
        max_results: int = 60,
        known_place_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Scraping com cota de empresas NOVAS (sem site).

        max_results = quantas empresas NOVAS sem site queremos, NÃO quantos
        resultados do Maps abrir. Já no banco / com site NÃO contam na cota;
        o bot continua o feed até encher a cota ou acabar a lista.
        """
        # Cota = leads novos sem site
        target_new = max(1, max_results)
        # Pool de URLs maior que a cota (muitas serão skip)
        url_pool_target = min(max(target_new * 5, 60), 120)

        companies: list[dict[str, Any]] = []
        known_ids: set[str] = set(known_place_ids or [])
        skipped_known = 0
        skipped_has_site = 0
        inspected = 0

        encoded_query = urllib.parse.quote_plus(query)
        maps_url = f"https://www.google.com/maps/search/{encoded_query}"

        page = self._new_page()
        try:
            logger.info(
                f"[Playwright] Abrindo: {maps_url} "
                f"(meta: {target_new} NOVAS sem site)"
            )
            page.goto(maps_url, wait_until="domcontentloaded", timeout=_TIMEOUT_MS)
            _random_delay(1.5, 3.0)
            self._dismiss_consent(page)

            try:
                page.wait_for_selector(_SEL_RESULTS_FEED, timeout=_TIMEOUT_MS)
            except PlaywrightTimeout:
                logger.warning(
                    "[Playwright] Feed de resultados não encontrado. "
                    "Possivelmente zero resultados ou bloqueio."
                )
                return companies

            if _is_blocked(page):
                logger.error(
                    "[Playwright] Google detectou tráfego automatizado. "
                    "Aguardando 60s..."
                )
                time.sleep(60)
                return companies

            # Coleta um pool amplo (já no banco não contam na cota depois)
            result_urls = self._collect_result_urls(page, url_pool_target)
            logger.info(
                f"[Playwright] Pool de {len(result_urls)} URLs "
                f"(meta {target_new} novas sem site)."
            )

            if not result_urls:
                logger.warning("[Playwright] Nenhuma URL de resultado encontrada.")
                return companies

            for idx, result_url in enumerate(result_urls, start=1):
                if len(companies) >= target_new:
                    break

                place_guess = _extract_place_id_from_url(result_url)
                if place_guess and place_guess in known_ids:
                    skipped_known += 1
                    logger.info(
                        f"[Playwright] ⏭ já no banco "
                        f"({len(companies)}/{target_new} novas) — pula painel"
                    )
                    continue

                logger.info(
                    f"[Playwright] Extraindo candidata "
                    f"({len(companies)}/{target_new} novas | "
                    f"url {idx}/{len(result_urls)}): {result_url[:70]}..."
                )
                try:
                    company = self._extract_details(
                        page, result_url, niche, city, state
                    )
                except PlaywrightTimeout:
                    logger.warning("[Playwright] Timeout ao extrair. Pulando.")
                    _random_delay(1.0, 2.0)
                    continue
                except Exception as exc:
                    logger.error(f"[Playwright] Erro no resultado {idx}: {exc}")
                    continue

                inspected += 1
                if not company or not company.get("name"):
                    continue

                pid = company.get("place_id") or place_guess
                if pid and pid in known_ids:
                    skipped_known += 1
                    logger.info(
                        f"[Playwright] ⏭ {company['name']!r} já processada "
                        f"({len(companies)}/{target_new} novas)"
                    )
                    continue

                # Tem site próprio → não conta na cota de leads
                if _has_own_website(company.get("website")):
                    skipped_has_site += 1
                    if pid:
                        known_ids.add(pid)  # não reabrir se aparecer de novo
                    logger.info(
                        f"[Playwright] ⏭ {company['name']!r} tem site — "
                        f"não conta ({len(companies)}/{target_new} novas)"
                    )
                    _random_delay(0.4, 1.0)
                    continue

                # NOVA sem site → conta na cota
                companies.append(company)
                if pid:
                    known_ids.add(pid)
                logger.info(
                    f"[Playwright] ✓ NOVA sem site "
                    f"[{len(companies)}/{target_new}]: {company['name']!r}"
                )

                if idx % 10 == 0 and _is_blocked(page):
                    logger.error(
                        f"[Playwright] Bloqueio após {idx} extrações. Aguardando 60s..."
                    )
                    time.sleep(60)

                _random_delay(_DELAY_MIN, _DELAY_MAX)

            logger.info(
                f"[Playwright] Fim da query: {len(companies)}/{target_new} novas sem site | "
                f"{skipped_known} já no banco | {skipped_has_site} com site | "
                f"{inspected} painéis abertos"
            )

        except Exception as exc:
            logger.error(f"[Playwright] Erro crítico durante o scraping: {exc}")
        finally:
            try:
                page.close()
            except Exception:
                pass

        return companies

    # ------------------------------------------------------------------
    # Etapa 1: Coletar URLs dos resultados
    # ------------------------------------------------------------------

    def _collect_result_urls(self, page: Page, max_results: int) -> list[str]:
        """
        Faz scroll no feed de resultados do Google Maps e coleta
        as URLs de cada resultado encontrado.

        O feed usa rolagem infinita, então precisamos fazer scroll
        repetidamente até atingir max_results ou o fim da lista.

        Returns:
            Lista de URLs únicas dos resultados.
        """
        urls: list[str] = []
        seen: set[str] = set()
        no_new_count = 0
        max_scroll_attempts = math.ceil(max_results / 5) + 10  # heurística

        feed = page.locator(_SEL_RESULTS_FEED)

        for attempt in range(max_scroll_attempts):
            if len(urls) >= max_results:
                break

            # Coleta todos os links de resultado visíveis agora
            all_links = page.locator(_SEL_RESULT_ITEMS).all()
            new_found = 0

            for link in all_links:
                try:
                    href = link.get_attribute("href") or ""
                    if "/maps/place/" in href and href not in seen:
                        seen.add(href)
                        urls.append(href)
                        new_found += 1
                        if len(urls) >= max_results:
                            break
                except Exception:
                    continue

            logger.debug(
                f"[Playwright] Scroll {attempt + 1}: "
                f"+{new_found} novos | total: {len(urls)}"
            )

            # Verifica se chegou ao fim da lista
            end_of_list = page.locator("span:has-text('Você chegou ao fim da lista')").count()
            if end_of_list > 0:
                logger.info("[Playwright] Fim da lista de resultados atingido.")
                break

            if new_found == 0:
                no_new_count += 1
                if no_new_count >= 3:
                    logger.info(
                        "[Playwright] Sem novos resultados após 3 tentativas de scroll. "
                        "Encerrando coleta."
                    )
                    break
            else:
                no_new_count = 0

            # Faz scroll dentro do feed de resultados
            try:
                feed.evaluate("el => el.scrollBy(0, el.scrollHeight)")
                _random_delay(1.2, 2.5)
            except Exception:
                # Fallback: scroll na página inteira
                page.keyboard.press("End")
                _random_delay(1.0, 2.0)

        return urls[:max_results]

    # ------------------------------------------------------------------
    # Etapa 2: Extrair detalhes de uma empresa
    # ------------------------------------------------------------------

    def _extract_details(
        self,
        page: Page,
        result_url: str,
        niche: str,
        city: str,
        state: str,
    ) -> dict[str, Any]:
        """
        Navega até a URL de um resultado específico e extrai todos
        os dados disponíveis no painel de detalhes.

        Args:
            page:       Instância da página do Playwright
            result_url: URL do resultado (ex: /maps/place/Nome/@lat,lon,...)
            niche:      Nicho da busca
            city:       Cidade da busca
            state:      Estado da busca

        Returns:
            Dicionário com os dados extraídos da empresa.
        """
        company = _build_empty_company(niche, city, state)

        # Monta URL absoluta se necessário
        if result_url.startswith("/"):
            full_url = f"https://www.google.com{result_url}"
        else:
            full_url = result_url

        company["maps_url"] = full_url
        company["place_id"] = _extract_place_id_from_url(full_url)
        lat, lon = _extract_coordinates_from_url(full_url)
        company["latitude"] = lat
        company["longitude"] = lon

        # Navega até a página de detalhes
        page.goto(full_url, wait_until="domcontentloaded", timeout=_TIMEOUT_MS)
        _random_delay(1.0, 2.0)

        # Aguarda o painel de detalhes carregar (pelo nome h1)
        try:
            page.wait_for_selector(_SEL_PANEL_NAME, timeout=_TIMEOUT_MS)
        except PlaywrightTimeout:
            logger.warning(f"[Playwright] Painel de detalhes não carregou para: {full_url}")
            return company

        # Atualiza coordenadas da URL final (pode ter redirecionado)
        final_url = page.url
        company["maps_url"] = final_url
        if not company["place_id"]:
            company["place_id"] = _extract_place_id_from_url(final_url)
        if company["latitude"] is None:
            lat, lon = _extract_coordinates_from_url(final_url)
            company["latitude"] = lat
            company["longitude"] = lon

        # ── Nome ────────────────────────────────────────────────────────
        company["name"] = self._get_text(page, _SEL_PANEL_NAME)

        # ── Avaliação ───────────────────────────────────────────────────
        rating_raw = self._get_text(page, _SEL_PANEL_RATING)
        company["rating"] = _parse_rating(rating_raw)

        # ── Número de avaliações ────────────────────────────────────────
        reviews_raw = self._get_aria_label(page, _SEL_PANEL_REVIEWS)
        company["review_count"] = _parse_review_count(reviews_raw)

        # ── Endereço ────────────────────────────────────────────────────
        company["address"] = self._get_button_text(page, _SEL_PANEL_ADDRESS)

        # ── Telefone ────────────────────────────────────────────────────
        raw_phone = self._get_button_text(page, _SEL_PANEL_PHONE)
        company["phone"] = _normalize_phone(raw_phone)

        # ── Website ─────────────────────────────────────────────────────
        company["website"] = self._get_href(page, _SEL_PANEL_WEBSITE)

        # ── Categoria ───────────────────────────────────────────────────
        company["category"] = self._get_text(page, _SEL_PANEL_CATEGORY)

        # ── Status aberto/fechado ───────────────────────────────────────
        open_text = self._get_text(page, _SEL_PANEL_OPEN_STATUS).lower()
        if "aberto" in open_text or "open" in open_text:
            company["is_open_now"] = 1
        elif "fechado" in open_text or "closed" in open_text:
            company["is_open_now"] = 0

        # ── Horários de funcionamento ───────────────────────────────────
        company["opening_hours"] = self._extract_hours(page)

        # ── Timestamp ───────────────────────────────────────────────────
        company["scraped_at"] = datetime.now().isoformat()
        company["source"] = "playwright"

        return company

    # ------------------------------------------------------------------
    # Extração de aceite de cookies/LGPD
    # ------------------------------------------------------------------

    def _dismiss_consent(self, page: Page) -> None:
        """
        Tenta fechar banners de consentimento de cookies do Google.

        O Google exibe diferentes variações do banner; tentamos os
        seletores mais comuns.
        """
        consent_selectors = [
            "button:has-text('Aceitar tudo')",
            "button:has-text('Accept all')",
            "button:has-text('Concordo')",
            "button:has-text('Rejeitar tudo')",
            "form[action*='consent'] button",
            "#L2AGLb",   # ID do botão "Aceitar tudo" em algumas versões
        ]
        for sel in consent_selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2000):
                    btn.click()
                    logger.debug(f"[Playwright] Banner de consentimento fechado: {sel}")
                    _random_delay(0.5, 1.0)
                    return
            except Exception:
                continue

    # ------------------------------------------------------------------
    # Helpers de extração de texto/atributos
    # ------------------------------------------------------------------

    def _get_text(self, page: Page, selector: str) -> str:
        """Retorna o text_content do primeiro elemento que bate com o seletor."""
        try:
            el = page.locator(selector).first
            text = el.text_content(timeout=3000)
            return (text or "").strip()
        except Exception:
            return ""

    def _get_aria_label(self, page: Page, selector: str) -> str:
        """Retorna o atributo aria-label do primeiro elemento encontrado."""
        try:
            el = page.locator(selector).first
            label = el.get_attribute("aria-label", timeout=3000)
            return (label or "").strip()
        except Exception:
            return ""

    def _get_button_text(self, page: Page, selector: str) -> str:
        """
        Retorna o texto de um botão de dado (endereço, telefone etc.).

        O Google Maps mostra esses dados em botões com aria-label
        contendo tanto o label quanto o valor.
        """
        try:
            el = page.locator(selector).first
            # Tenta pegar via aria-label (mais confiável)
            label = el.get_attribute("aria-label", timeout=3000) or ""
            if label:
                # O aria-label costuma ser "Endereço: Rua X, 123" — pega só o valor
                parts = label.split(":", 1)
                return parts[1].strip() if len(parts) > 1 else label.strip()
            # Fallback: texto visível do botão
            return (el.text_content(timeout=3000) or "").strip()
        except Exception:
            return ""

    def _get_href(self, page: Page, selector: str) -> str:
        """Retorna o href do primeiro link que bate com o seletor."""
        try:
            el = page.locator(selector).first
            href = el.get_attribute("href", timeout=3000) or ""
            # Remove prefixo de redirecionamento do Google (/url?q=...)
            if href.startswith("/url?q="):
                href = urllib.parse.unquote(href.split("q=", 1)[1].split("&")[0])
            return href.strip()
        except Exception:
            return ""

    def _extract_hours(self, page: Page) -> str:
        """
        Extrai os horários de funcionamento da tabela de horários.

        O Google exibe os horários em uma tabela expandível; tentamos
        ler as linhas da tabela diretamente.

        Returns:
            String com horários no formato "Seg: 09:00–22:00 | Ter: ..."
            ou string vazia se não encontrado.
        """
        try:
            # Tenta expandir o bloco de horários clicando nele
            toggle = page.locator(
                "div.t39EBf, [data-item-id='oh'] button, .OMl5r"
            ).first
            if toggle.is_visible(timeout=2000):
                toggle.click()
                _random_delay(0.3, 0.8)
        except Exception:
            pass

        try:
            rows = page.locator(f"{_SEL_PANEL_HOURS} tr").all()
            hours_parts = []
            for row in rows:
                cells = row.locator("td").all()
                if len(cells) >= 2:
                    day = (cells[0].text_content() or "").strip()
                    time_range = (cells[1].text_content() or "").strip()
                    if day and time_range:
                        hours_parts.append(f"{day}: {time_range}")
            return " | ".join(hours_parts) if hours_parts else ""
        except Exception:
            return ""


# ---------------------------------------------------------------------------
# Classe principal — fachada pública do módulo
# ---------------------------------------------------------------------------

class GoogleMapsSearcher:
    """
    Interface principal do módulo de busca no Google Maps.

    Coordena as estratégias de busca (API ou scraping) e persiste
    os resultados no banco de dados SQLite.

    Uso:
        searcher = GoogleMapsSearcher()
        leads = searcher.search(
            niche="restaurante",
            city="Porto Alegre",
            state="RS",
            max_results=100,
        )
    """

    def __init__(self, db_path: str = _DB_PATH) -> None:
        self.db = Database(db_path)
        self._api_client: PlacesAPIClient | None = (
            PlacesAPIClient(_API_KEY) if _API_KEY else None
        )
        if self._api_client:
            logger.info("[GoogleMapsSearcher] Google Places API configurada.")
        else:
            logger.info(
                "[GoogleMapsSearcher] API key não configurada. "
                "Usando apenas Playwright (scraping)."
            )

    # ------------------------------------------------------------------
    # Método principal
    # ------------------------------------------------------------------

    @staticmethod
    def _build_query_variants(
        term: str,
        city: str,
        state: str,
        focus_area: str | None = None,
        bairros: list[str] | None = None,
    ) -> list[str]:
        """
        Variações enxutas:
        - Com focus_area (bairro): 2 queries focadas (rápido, sem repetir cidade inteira)
        - Sem foco: bairros da lista + poucas genéricas
        """
        term = term.strip()
        base: list[str] = []
        area = (focus_area or "").strip()
        if area and area != "_cidade":
            base = [
                f"{term} {area} {city}",
                f"{term} em {area} {city} {state}",
                f"{term} {area} {state}",
            ]
        else:
            base = [f"{term} em {city} {state}"]
            for b in (bairros or [])[:6]:
                b = (b or "").strip()
                if b:
                    base.append(f"{term} {b} {city}")
            base.extend(
                [
                    f"{term} {city} centro",
                    f"{term} perto de {city} {state}",
                ]
            )

        seen: set[str] = set()
        out: list[str] = []
        for q in base:
            if q not in seen:
                seen.add(q)
                out.append(q)
        return out

    def search(
        self,
        niche: str,
        city: str,
        state: str,
        max_results: int = 60,
        query_term: str | None = None,
        bairros: list[str] | None = None,
        focus_area: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Busca empresas NOVAS sem site no Google Maps.

        max_results = meta de leads novos (não contam: já no banco, com site).
        focus_area = bairro atual (job unitário) — evita rebuscar a cidade inteira.
        """
        term = (query_term or niche).strip()
        target_new = max(1, max_results)
        variants = self._build_query_variants(
            term, city, state, focus_area=focus_area, bairros=bairros
        )

        area_label = focus_area if focus_area and focus_area != "_cidade" else "cidade"
        logger.info(
            f"[GoogleMapsSearcher] Meta: {target_new} NOVAS sem site | "
            f"{niche} / {city}-{state} / {area_label} | {len(variants)} queries"
        )

        from src.checkpoint import CompanyCheckpoint
        known = CompanyCheckpoint.load()
        known_ids = known.as_set()

        kept: list[dict[str, Any]] = []
        saved_total = 0
        skipped_exists = 0
        skipped_has_site = 0

        for v_idx, query in enumerate(variants, start=1):
            if saved_total >= target_new:
                break

            remaining = target_new - saved_total
            logger.info(
                f"[GoogleMapsSearcher] Variação {v_idx}/{len(variants)}: {query!r} "
                f"(faltam {remaining} novas)"
            )

            batch: list[dict[str, Any]] = []

            # API se disponível
            if self._api_client:
                batch = self._search_via_api(
                    query, niche, city, state, remaining, known_ids=known_ids
                )

            # Playwright (primário ou se API não encheu)
            if len(batch) < remaining:
                if self._api_client and batch:
                    logger.info(
                        f"[GoogleMapsSearcher] API trouxe {len(batch)}; "
                        f"completando com Playwright..."
                    )
                elif self._api_client and not batch:
                    logger.warning(
                        "[GoogleMapsSearcher] API vazia — Playwright..."
                    )
                pw_batch = self._search_via_playwright(
                    query,
                    niche,
                    city,
                    state,
                    remaining - len(batch),
                    known_place_ids=known_ids,
                )
                # evita duplicar place_id no batch
                seen_batch = {c.get("place_id") for c in batch if c.get("place_id")}
                for c in pw_batch:
                    pid = c.get("place_id")
                    if pid and pid in seen_batch:
                        continue
                    batch.append(c)
                    if pid:
                        seen_batch.add(pid)

            for company in batch:
                if saved_total >= target_new:
                    break
                place_id = company.get("place_id", "")
                if place_id and (place_id in known_ids or self.db.place_id_exists(place_id)):
                    skipped_exists += 1
                    known_ids.add(place_id)
                    continue
                if _has_own_website(company.get("website")):
                    skipped_has_site += 1
                    if place_id:
                        known_ids.add(place_id)
                    continue
                try:
                    row_id = self.db.upsert_company(company)
                    company["id"] = row_id
                    saved_total += 1
                    kept.append(company)
                    if place_id:
                        known_ids.add(place_id)
                    logger.info(
                        f"[GoogleMapsSearcher] Salva NOVA "
                        f"[{saved_total}/{target_new}]: {company.get('name')!r}"
                    )
                    # Score na hora → se parar o bot, lead já aparece no dashboard
                    self._score_immediately(row_id)
                except Exception as exc:
                    logger.error(
                        f"[DB] Erro ao salvar {company.get('name', '?')!r}: {exc}"
                    )

        logger.info(
            f"[GoogleMapsSearcher] Concluído: {saved_total}/{target_new} NOVAS sem site | "
            f"{skipped_has_site} com site | {skipped_exists} já no banco"
        )
        return kept

    # ------------------------------------------------------------------
    # Busca via Places API
    # ------------------------------------------------------------------

    def _score_immediately(self, company_id: int) -> None:
        """Classifica o lead assim que é salvo (sem esperar o fim do lote)."""
        if not company_id:
            return
        try:
            if not hasattr(self, "_scorer") or self._scorer is None:
                from src.scorer import LeadScorer
                self._scorer = LeadScorer()
            self._scorer.score_one(company_id)
        except Exception as exc:
            logger.warning(
                f"[GoogleMapsSearcher] Score imediato falhou id={company_id}: {exc}"
            )

    def _search_via_api(
        self,
        query: str,
        niche: str,
        city: str,
        state: str,
        max_results: int,
        known_place_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """API: busca até max_results NOVAS sem site (já no banco não contam)."""
        logger.info("[Places API] Executando busca via API...")
        known = set(known_place_ids or [])
        try:
            # pede mais resultados brutos — muitos serão skip
            raw_results = self._api_client.text_search(
                query, min(max_results * 4, 60)
            )
        except Exception as exc:
            logger.error(f"[Places API] Falha na busca: {exc}")
            return []

        companies: list[dict[str, Any]] = []
        for idx, raw in enumerate(raw_results, start=1):
            if len(companies) >= max_results:
                break
            place_id = raw.get("place_id", "")

            if place_id and (place_id in known or self.db.place_id_exists(place_id)):
                logger.debug(f"[Places API] {idx}: já no banco — não conta na cota.")
                if place_id:
                    known.add(place_id)
                continue

            logger.debug(f"[Places API] Detalhes: {raw.get('name')} ({place_id})")
            details = self._api_client.get_place_details(place_id) if place_id else raw
            merged = {**raw, **details}

            company = self._api_client.normalize_place(merged, niche, city, state)
            if _has_own_website(company.get("website")):
                logger.debug(f"[Places API] Skip (tem site): {company.get('name')!r}")
                if place_id:
                    known.add(place_id)
                continue

            companies.append(company)
            if place_id:
                known.add(place_id)
            _random_delay(0.5, 1.5)

        logger.info(
            f"[Places API] {len(companies)} NOVAS sem site (cota {max_results})."
        )
        return companies

    # ------------------------------------------------------------------
    # Busca via Playwright
    # ------------------------------------------------------------------

    def _search_via_playwright(
        self,
        query: str,
        niche: str,
        city: str,
        state: str,
        max_results: int,
        known_place_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Playwright: max_results = meta de NOVAS sem site."""
        logger.info("[Playwright] Executando busca via scraping...")
        try:
            if known_place_ids is None:
                from src.checkpoint import CompanyCheckpoint
                known_place_ids = CompanyCheckpoint.load().as_set()
            with PlaywrightScraper() as scraper:
                return scraper.scrape(
                    query,
                    niche,
                    city,
                    state,
                    max_results,
                    known_place_ids=known_place_ids,
                )
        except Exception as exc:
            logger.error(f"[Playwright] Falha crítica no scraping: {exc}")
            return []

    # ------------------------------------------------------------------
    # Funções auxiliares públicas
    # ------------------------------------------------------------------

    def search_by_coordinates(
        self,
        niche: str,
        lat: float,
        lon: float,
        radius_m: int = 5000,
        max_results: int = 60,
        city: str = "",
        state: str = "",
    ) -> list[dict[str, Any]]:
        """
        Busca empresas próximas a coordenadas geográficas (requer API key).

        Usa o endpoint nearbysearch da Google Places API, que aceita
        um ponto central (lat, lon) e um raio em metros.

        Args:
            niche:      Tipo de negócio a buscar
            lat:        Latitude do centro da busca
            lon:        Longitude do centro da busca
            radius_m:   Raio de busca em metros (máx: 50000)
            max_results: Número máximo de resultados
            city:       Cidade para registro no banco (opcional)
            state:      Estado para registro no banco (opcional)

        Returns:
            Lista de empresas encontradas no formato padrão.

        Raises:
            RuntimeError: Se GOOGLE_MAPS_API_KEY não estiver configurada.
        """
        if not self._api_client:
            raise RuntimeError(
                "search_by_coordinates() requer GOOGLE_MAPS_API_KEY configurada no .env"
            )

        logger.info(
            f"[GoogleMapsSearcher] Busca por coordenadas: "
            f"niche={niche!r} lat={lat} lon={lon} raio={radius_m}m"
        )

        try:
            response = self._api_client.session.get(
                _PLACES_NEARBY_URL,
                params={
                    "location": f"{lat},{lon}",
                    "radius": radius_m,
                    "keyword": niche,
                    "key": _API_KEY,
                    "language": "pt-BR",
                },
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            raw_results = data.get("results", [])
        except requests.RequestException as exc:
            logger.error(f"[Places API Nearby] Erro na requisição: {exc}")
            return []

        companies = [
            self._api_client.normalize_place(raw, niche, city, state)
            for raw in raw_results[:max_results]
        ]

        for company in companies:
            try:
                self.db.upsert_company(company)
            except Exception as exc:
                logger.error(f"[DB] Erro ao salvar: {exc}")

        logger.info(f"[GoogleMapsSearcher] {len(companies)} empresas encontradas por coordenadas.")
        return companies

    def get_place_details(self, place_id: str) -> dict[str, Any]:
        """
        Retorna dados detalhados de um lugar pelo Place ID do Google.

        Útil para enriquecer leads já salvos no banco com informações
        que não foram capturadas durante o scraping inicial.

        Args:
            place_id: ID do lugar no Google Places (ex: "ChIJ...")

        Returns:
            Dicionário com os detalhes do lugar (ou {} se não encontrado).

        Raises:
            RuntimeError: Se GOOGLE_MAPS_API_KEY não estiver configurada.
        """
        if not self._api_client:
            raise RuntimeError(
                "get_place_details() requer GOOGLE_MAPS_API_KEY configurada no .env"
            )
        return self._api_client.get_place_details(place_id)


# ---------------------------------------------------------------------------
# Execução direta (modo CLI / debug)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    # Configura o logger para saída legível no terminal
    logger.remove()
    logger.add(
        sink=lambda msg: print(msg, end=""),
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        colorize=True,
        level="DEBUG",
    )

    # Exemplo de uso direto:
    # python src/google_maps.py
    searcher = GoogleMapsSearcher()
    results = searcher.search(
        niche="restaurante",
        city="Porto Alegre",
        state="RS",
        max_results=10,
    )

    print(f"\n{'='*60}")
    print(f"  Resultados encontrados: {len(results)}")
    print(f"{'='*60}\n")

    for i, company in enumerate(results, 1):
        print(f"[{i:02d}] {company['name']}")
        print(f"      📍 {company['address']}")
        print(f"      📞 {company['phone'] or 'N/A'}")
        print(f"      🌐 {company['website'] or 'N/A'}")
        print(f"      ⭐ {company['rating']} ({company['review_count']} avaliações)")
        print(f"      🏷️  {company['category']}")
        print(f"      📌 {company['latitude']}, {company['longitude']}")
        print(f"      🔑 source={company['source']}")
        print()
