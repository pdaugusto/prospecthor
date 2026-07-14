"""
website_checker.py — Verificação de presença e qualidade de site
=================================================================

Verifica se cada empresa possui site próprio e, caso possua,
analisa múltiplos critérios de qualidade, gerando um score de
oportunidade de venda de serviços digitais.

Filosofia de pontuação:
    Score ALTO = site fraco = MAIOR oportunidade de venda.
    Score ZERO = site excelente = menor interesse comercial.

Classificações de status do site:
    "sem_site"          → Empresa sem URL cadastrada (+30 pts)
    "so_social"         → URL é só Facebook/Instagram (+25 pts)
    "site_fora"         → URL existe mas não responde (+20 pts)
    "erro_ssl"          → Site com certificado SSL inválido (+18 pts)
    "timeout"           → Site respondeu mas excedeu 10s (+15 pts)
    "template_generico" → Site com texto de template/placeholder (+15 pts)
    "em_construcao"     → Site ainda não publicado (+20 pts)
    "ok"                → Site no ar, avalia subcritérios abaixo

Subcritérios (aplicados quando status == "ok"):
    nao_mobile   → Sem viewport meta tag (+10 pts)
    sem_https    → URL sem HTTPS (+5 pts)
    lento        → Carregamento > 5s (+5 pts)
    sem_contato  → Sem telefone/email/WhatsApp visível (+5 pts)

Uso:
    from src.website_checker import WebsiteChecker

    checker = WebsiteChecker()

    # Verificar uma empresa específica:
    result = checker.check_company({"id": 1, "website": "http://exemplo.com"})

    # Verificar todas as empresas pendentes do banco:
    results = checker.check_all(limit=50)
"""

from __future__ import annotations

import os
import re
import ssl
import time
import socket
import psycopg2
import psycopg2.extras
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from loguru import logger
from requests.exceptions import (
    ConnectionError as ReqConnectionError,
    SSLError,
    Timeout,
    TooManyRedirects,
)

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

load_dotenv()

_DATABASE_URL: str = os.getenv("DATABASE_URL", "")
_DB_PATH: str = os.getenv("DATABASE_PATH", "data/leads.db")
_HTTP_TIMEOUT: int = 10            # Timeout máximo por requisição (segundos)
_SLOW_THRESHOLD_S: float = 5.0     # Acima disso, site é considerado "lento"
_MAX_REDIRECTS: int = 5            # Máximo de redirecionamentos a seguir

# Headers que imitam um navegador real (reduz taxa de bloqueio por WAFs)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}

# ---------------------------------------------------------------------------
# Domínios de redes sociais que indicam "só tem social"
# ---------------------------------------------------------------------------

_SOCIAL_DOMAINS: frozenset[str] = frozenset(
    [
        "facebook.com",
        "fb.com",
        "instagram.com",
        "twitter.com",
        "x.com",
        "tiktok.com",
        "linkedin.com",
        "youtube.com",
        "whatsapp.com",
        "wa.me",
        "linktr.ee",
        "bio.link",
        "beacons.ai",
    ]
)

# ---------------------------------------------------------------------------
# Strings que revelam sites de template / em construção
# ---------------------------------------------------------------------------

_TEMPLATE_SIGNALS: list[str] = [
    "lorem ipsum",
    "sample page",
    "sample post",
    "hello world",
    "coming soon",
    "em construção",
    "em breve",
    "site em construção",
    "under construction",
    "página de exemplo",
    "este é um exemplo",
    "default wordpress",
    "just another wordpress",
    "proudly powered by wordpress",    # site padrão sem personalização
    "sitio de ejemplo",
]

# Textos que indicam domínio à venda ou estacionado
_PARKED_SIGNALS: list[str] = [
    "domain for sale",
    "domínio à venda",
    "buy this domain",
    "this domain is parked",
    "domain parking",
    "godaddy.com/domains",
    "register4less",
    "sedo.com",
    "dan.com",
    "this domain may be for sale",
]

# Padrões regex de telefone e WhatsApp em HTML
_CONTACT_PATTERNS: list[re.Pattern] = [
    re.compile(r"\(?\d{2}\)?\s?[\d\s\-]{8,10}"),            # telefones BR
    re.compile(r"\+55\s?\d{2}\s?[\d\s\-]{8,10}"),           # +55 DDD
    re.compile(r"wa\.me/\d+"),                               # link WhatsApp
    re.compile(r"api\.whatsapp\.com/send"),                  # WhatsApp API
    re.compile(r"[\w.\-]+@[\w.\-]+\.[a-zA-Z]{2,}"),         # e-mail
    re.compile(r"tel:\+?\d[\d\s\-\(\)]{7,}"),               # href tel:
    re.compile(r"mailto:[\w.\-]+@[\w.\-]+"),                 # href mailto:
]

# CMS detectados por fingerprint no HTML
_CMS_FINGERPRINTS: list[tuple[str, str]] = [
    ("WordPress",    r"wp-content|wp-includes|wordpress"),
    ("Wix",         r'wix\.com|wixstatic\.com|"wix"'),
    ("Shopify",     r'shopify\.com|cdn\.shopify'),
    ("Squarespace", r'squarespace\.com|sqsp\.net'),
    ("Webflow",     r'webflow\.com|webflow\.io'),
    ("Joomla",      r'/components/com_|joomla'),
    ("Drupal",      r'drupal\.org|drupal\.js|sites/default/files'),
    ("VTEX",        r'vtex\.com|vtexcommercestable'),
    ("Loja Integrada", r'lojaintegrada\.com\.br'),
    ("Nuvemshop",   r'nuvemshop\.com\.br|tiendanube\.com'),
    ("Blogger",     r'blogspot\.com|blogger\.com'),
    ("Ghost",       r'ghost\.org|ghost\.io'),
]

# ---------------------------------------------------------------------------
# Migração do banco de dados
# ---------------------------------------------------------------------------

# Colunas a adicionar na tabela companies (migration-safe)
_WEBSITE_COLUMNS: list[tuple[str, str]] = [
    ("website_status",       "TEXT"),     # sem_site|so_social|site_fora|ok|...
    ("website_flags",        "TEXT"),     # CSV: nao_mobile,sem_https,lento,sem_contato
    ("website_mobile",       "INTEGER"),  # 1=tem viewport, 0=não tem, NULL=não verificado
    ("website_https",        "INTEGER"),  # 1=https, 0=http, NULL=não verificado
    ("website_speed_s",      "REAL"),     # Tempo de resposta em segundos
    ("website_score",        "INTEGER"),  # Pontuação de oportunidade (0-100)
    ("website_cms",          "TEXT"),     # CMS detectado (WordPress, Wix, etc.)
    ("website_has_contact",  "INTEGER"),  # 1=tem contato visível, 0=não tem
    ("website_title",        "TEXT"),     # <title> da página
    ("website_checked_at",   "TEXT"),     # ISO-8601 da última verificação
]

_SAVE_WEBSITE_SQL = """
UPDATE companies SET
    website_status      = %(website_status)s,
    website_flags       = %(website_flags)s,
    website_mobile      = %(website_mobile)s,
    website_https       = %(website_https)s,
    website_speed_s     = %(website_speed_s)s,
    website_score       = %(website_score)s,
    website_cms         = %(website_cms)s,
    website_has_contact = %(website_has_contact)s,
    website_title       = %(website_title)s,
    website_checked_at  = %(website_checked_at)s
WHERE id = %(id)s;
"""

_SELECT_PENDING_SQL = """
SELECT id, name, website, city, state, niche
FROM companies
WHERE website_checked_at IS NULL
  AND (business_status IS NULL OR business_status != 'CLOSED_PERMANENTLY')
ORDER BY id
LIMIT %(limit)s;
"""

_SELECT_BY_ID_SQL = "SELECT id, name, website, city, state, niche FROM companies WHERE id = %s;"


# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------

class WebsiteDatabase:
    """
    Gerencia a conexão com o banco PostgreSQL para o módulo website_checker.
    """

    def __init__(self, db_path: str = _DB_PATH) -> None:
        # db_path mantido na assinatura para compatibilidade
        if not _DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL não configurada no .env. "
                "Defina a string de conexão PostgreSQL (ex: Supabase)."
            )
        self._migrate()

    def _connect(self):
        return psycopg2.connect(_DATABASE_URL)

    def _migrate(self) -> None:
        """
        Adiciona as colunas de website à tabela companies se ainda não existirem.
        """
        conn = self._connect()
        try:
            cur = conn.cursor()
            added = []
            for col_name, col_type in _WEBSITE_COLUMNS:
                cur.execute("""
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'companies' AND column_name = %s;
                """, (col_name,))
                if not cur.fetchone():
                    try:
                        cur.execute(
                            f"ALTER TABLE companies ADD COLUMN {col_name} {col_type};"
                        )
                        added.append(col_name)
                    except Exception as exc:
                        logger.warning(f"[DB] Coluna {col_name!r} não adicionada: {exc}")
            if added:
                conn.commit()
                logger.info(f"[DB] Migração: {len(added)} colunas adicionadas → {added}")
            cur.close()
        finally:
            conn.close()

    def get_pending(self, limit: int = 100) -> list[dict[str, Any]]:
        """
        Retorna empresas que ainda não tiveram o site verificado.
        """
        conn = self._connect()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(_SELECT_PENDING_SQL, {"limit": limit})
            rows = cur.fetchall()
            cur.close()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_by_id(self, company_id: int) -> dict[str, Any] | None:
        """Retorna uma empresa pelo ID."""
        conn = self._connect()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(_SELECT_BY_ID_SQL, (company_id,))
            row = cur.fetchone()
            cur.close()
            return dict(row) if row else None
        finally:
            conn.close()

    def save_result(self, result: dict[str, Any]) -> None:
        """
        Persiste o resultado da verificação de website para uma empresa.
        """
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(_SAVE_WEBSITE_SQL, result)
            conn.commit()
            cur.close()
            logger.debug(
                f"[DB] Salvo website_status={result['website_status']!r} "
                f"score={result['website_score']} para empresa id={result['id']}"
            )
        except Exception as exc:
            logger.error(f"[DB] Erro ao salvar resultado para id={result.get('id')}: {exc}")
        finally:
            conn.close()

    def get_stats(self) -> dict[str, Any]:
        """Retorna estatísticas gerais das verificações de website."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM companies;")
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM companies WHERE website_checked_at IS NOT NULL;")
            checked = cur.fetchone()[0]
            cur.execute("""
                SELECT website_status, COUNT(*) as cnt 
                FROM companies WHERE website_checked_at IS NOT NULL 
                GROUP BY website_status ORDER BY cnt DESC;
            """)
            by_status = cur.fetchall()
            cur.close()
            return {
                "total_companies": total,
                "checked": checked,
                "pending": total - checked,
                "by_status": {row[0]: row[1] for row in by_status},
            }
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Resultado padrão de verificação
# ---------------------------------------------------------------------------

def _empty_result(company_id: int) -> dict[str, Any]:
    """Retorna dicionário de resultado com todos os campos zerados."""
    return {
        "id": company_id,
        "website_status": "sem_site",
        "website_flags": "",
        "website_mobile": None,
        "website_https": None,
        "website_speed_s": None,
        "website_score": 0,
        "website_cms": None,
        "website_has_contact": None,
        "website_title": None,
        "website_checked_at": datetime.now().isoformat(),
    }


# ---------------------------------------------------------------------------
# Verificação individual de URL
# ---------------------------------------------------------------------------

class UrlChecker:
    """
    Responsável por fazer a requisição HTTP e extrair todas as
    métricas de qualidade de uma única URL.

    Cada instância representa a verificação de um site específico.
    Não deve ser reusada entre sites diferentes.
    """

    def __init__(self, url: str) -> None:
        # Garante que a URL tenha protocolo
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        self.original_url = url
        self.session = self._make_session()

    @staticmethod
    def _make_session() -> requests.Session:
        """Cria sessão requests com configurações anti-bloqueio."""
        session = requests.Session()
        session.headers.update(_HEADERS)
        session.max_redirects = _MAX_REDIRECTS
        return session

    # ------------------------------------------------------------------
    # Método principal
    # ------------------------------------------------------------------

    def check(self) -> dict[str, Any]:
        """
        Executa todas as verificações na URL e retorna o resultado.

        Sequência:
            1. Verifica se é URL de rede social → retorna "so_social"
            2. Faz requisição HTTP com timeout de 10s
            3. Trata erros de SSL, timeout, conexão
            4. Faz parse do HTML com BeautifulSoup
            5. Avalia critérios de qualidade
            6. Calcula score de oportunidade

        Returns:
            Dicionário com todos os campos website_* preenchidos.
        """
        # ── Verificação 0: É só rede social? ────────────────────────────
        if self._is_social_url(self.original_url):
            logger.debug(f"[URL] {self.original_url[:60]} → so_social")
            return {
                "status": "so_social",
                "flags": [],
                "mobile": None,
                "https": None,
                "speed_s": None,
                "cms": None,
                "has_contact": None,
                "title": None,
                "score": 25,
            }

        # ── Verificação 1: Faz a requisição HTTP ─────────────────────────
        fetch_result = self._fetch()

        if fetch_result["error"]:
            error_status = fetch_result["error"]
            score_map = {
                "site_fora":    20,
                "erro_ssl":     18,
                "timeout":      15,
                "too_many_redirects": 12,
                "sem_resposta": 20,
            }
            logger.debug(
                f"[URL] {self.original_url[:60]} → {error_status} "
                f"(score={score_map.get(error_status, 15)})"
            )
            return {
                "status": error_status,
                "flags": [],
                "mobile": None,
                "https": self.original_url.startswith("https"),
                "speed_s": fetch_result.get("speed_s"),
                "cms": None,
                "has_contact": None,
                "title": None,
                "score": score_map.get(error_status, 15),
            }

        html: str = fetch_result["html"]
        final_url: str = fetch_result["final_url"]
        speed_s: float = fetch_result["speed_s"]
        status_code: int = fetch_result["status_code"]

        # ── Verificação 2: Parse do HTML ──────────────────────────────────
        soup = BeautifulSoup(html, "lxml")

        # ── Verificação 3: Domínio estacionado / à venda ─────────────────
        if self._is_parked(soup, html):
            logger.debug(f"[URL] {self.original_url[:60]} → em_construcao (parked)")
            return {
                "status": "em_construcao",
                "flags": [],
                "mobile": None,
                "https": final_url.startswith("https"),
                "speed_s": speed_s,
                "cms": None,
                "has_contact": None,
                "title": self._get_title(soup),
                "score": 20,
            }

        # ── Verificação 4: Site de template / em construção ───────────────
        template_status = self._detect_template(soup, html)
        if template_status:
            logger.debug(f"[URL] {self.original_url[:60]} → {template_status}")
            return {
                "status": template_status,
                "flags": [],
                "mobile": self._has_viewport(soup),
                "https": final_url.startswith("https"),
                "speed_s": speed_s,
                "cms": self._detect_cms(html),
                "has_contact": False,
                "title": self._get_title(soup),
                "score": 15 if template_status == "template_generico" else 20,
            }

        # ── Verificação 5: Subcritérios (site "ok") ────────────────────
        flags: list[str] = []
        score = 0

        has_mobile = self._has_viewport(soup)
        if not has_mobile:
            flags.append("nao_mobile")
            score += 10

        uses_https = final_url.startswith("https")
        if not uses_https:
            flags.append("sem_https")
            score += 5

        is_slow = speed_s > _SLOW_THRESHOLD_S
        if is_slow:
            flags.append("lento")
            score += 5

        has_contact = self._has_contact(soup, html)
        if not has_contact:
            flags.append("sem_contato")
            score += 5

        cms = self._detect_cms(html)
        title = self._get_title(soup)

        logger.debug(
            f"[URL] {self.original_url[:60]} → ok "
            f"flags={flags} score={score} speed={speed_s:.2f}s"
        )

        return {
            "status": "ok",
            "flags": flags,
            "mobile": has_mobile,
            "https": uses_https,
            "speed_s": round(speed_s, 3),
            "cms": cms,
            "has_contact": has_contact,
            "title": title,
            "score": score,
        }

    # ------------------------------------------------------------------
    # Requisição HTTP
    # ------------------------------------------------------------------

    def _fetch(self) -> dict[str, Any]:
        """
        Realiza a requisição HTTP/HTTPS e retorna os dados da resposta.

        Tenta primeiro com HTTPS; se a URL for HTTP, usa diretamente.
        Trata SSL inválido, timeout, falha de conexão e redirecionamentos.

        Returns:
            Dicionário com: html, final_url, speed_s, status_code, error.
            Se error != None, os outros campos podem ser None/0.
        """
        # Primeiro tenta a URL original
        urls_to_try = [self.original_url]

        # Se a URL original for HTTP, adiciona versão HTTPS como tentativa extra
        if self.original_url.startswith("http://"):
            urls_to_try.insert(0, "https://" + self.original_url[7:])

        for url in urls_to_try:
            result = self._try_fetch(url)
            if not result["error"]:
                return result
            # Se HTTPS falhou com SSL, tenta HTTP (captura o caso de cert inválido)
            if result["error"] == "erro_ssl" and url.startswith("https://"):
                logger.debug(
                    f"[URL] SSL inválido em {url[:60]}, "
                    "tentando sem verificação de certificado..."
                )
                result_no_verify = self._try_fetch(url, verify_ssl=False)
                if not result_no_verify["error"]:
                    # Site acessível, mas SSL inválido — marca como erro_ssl
                    result_no_verify["error"] = "erro_ssl"
                    return result_no_verify

        # Retorna o último erro
        return result

    def _try_fetch(self, url: str, verify_ssl: bool = True) -> dict[str, Any]:
        """
        Tenta uma única requisição GET para a URL.

        Args:
            url:        URL completa para acessar
            verify_ssl: Se deve verificar o certificado SSL

        Returns:
            Dicionário com html, final_url, speed_s, status_code, error.
        """
        empty = {
            "html": "",
            "final_url": url,
            "speed_s": None,
            "status_code": None,
            "error": None,
        }

        try:
            t0 = time.monotonic()
            response = self.session.get(
                url,
                timeout=_HTTP_TIMEOUT,
                verify=verify_ssl,
                allow_redirects=True,
                stream=False,
            )
            speed_s = time.monotonic() - t0

            # Trata códigos de erro HTTP
            if response.status_code >= 500:
                empty["error"] = "site_fora"
                empty["speed_s"] = round(speed_s, 3)
                empty["status_code"] = response.status_code
                return empty

            # 4xx pode significar que o site existe mas bloqueia bots
            # Tratamos como "ok" para análise do HTML disponível
            html = response.text
            return {
                "html": html,
                "final_url": response.url,
                "speed_s": round(speed_s, 3),
                "status_code": response.status_code,
                "error": None,
            }

        except SSLError as exc:
            logger.debug(f"[URL] SSL Error em {url[:60]}: {exc}")
            empty["error"] = "erro_ssl"
            return empty

        except Timeout:
            logger.debug(f"[URL] Timeout em {url[:60]}")
            empty["error"] = "timeout"
            empty["speed_s"] = _HTTP_TIMEOUT
            return empty

        except TooManyRedirects:
            logger.debug(f"[URL] Muitos redirecionamentos em {url[:60]}")
            empty["error"] = "too_many_redirects"
            return empty

        except ReqConnectionError as exc:
            logger.debug(f"[URL] Conexão recusada em {url[:60]}: {exc}")
            empty["error"] = "site_fora"
            return empty

        except Exception as exc:
            logger.debug(f"[URL] Erro inesperado em {url[:60]}: {exc}")
            empty["error"] = "sem_resposta"
            return empty

    # ------------------------------------------------------------------
    # Detecções de conteúdo
    # ------------------------------------------------------------------

    @staticmethod
    def _is_social_url(url: str) -> bool:
        """
        Retorna True se a URL pertence a uma rede social ou link-in-bio.

        Exemplos detectados:
            facebook.com/minha-empresa
            instagram.com/minha_empresa
            linktr.ee/username
        """
        try:
            parsed = urllib.parse.urlparse(url)
            domain = parsed.netloc.lower()
            # Remove www.
            domain = domain.removeprefix("www.")
            return any(domain == s or domain.endswith("." + s) for s in _SOCIAL_DOMAINS)
        except Exception:
            return False

    @staticmethod
    def _is_parked(soup: BeautifulSoup, html: str) -> bool:
        """Detecta domínios estacionados ou à venda."""
        html_lower = html.lower()
        return any(signal in html_lower for signal in _PARKED_SIGNALS)

    @staticmethod
    def _detect_template(soup: BeautifulSoup, html: str) -> str | None:
        """
        Detecta se o site contém textos típicos de template ou página em
        construção. Retorna o status correspondente ou None se site parece real.

        Returns:
            "template_generico" | "em_construcao" | None
        """
        html_lower = html.lower()
        body_text = soup.get_text(separator=" ", strip=True).lower() if soup.body else html_lower

        # Strings de "em construção" têm prioridade
        construction_signals = [
            "em construção", "em breve", "coming soon",
            "under construction", "site em construção",
        ]
        if any(s in body_text for s in construction_signals):
            return "em_construcao"

        # Strings de template genérico
        if any(s in body_text for s in _TEMPLATE_SIGNALS):
            return "template_generico"

        return None

    @staticmethod
    def _has_viewport(soup: BeautifulSoup) -> bool:
        """
        Verifica se a página contém a meta tag viewport,
        indicativo de design responsivo/mobile-friendly.

        Exemplo de tag detectada:
            <meta name="viewport" content="width=device-width, initial-scale=1">
        """
        viewport = soup.find("meta", attrs={"name": re.compile(r"viewport", re.I)})
        return viewport is not None

    @staticmethod
    def _has_contact(soup: BeautifulSoup, html: str) -> bool:
        """
        Verifica se a página contém informações de contato visíveis:
        telefone, email, link do WhatsApp ou botão de contato.

        Usa regex sobre o HTML bruto para capturar tanto texto visível
        quanto atributos href (tel:, mailto:, wa.me/).

        Returns:
            True se pelo menos um padrão de contato for encontrado.
        """
        for pattern in _CONTACT_PATTERNS:
            if pattern.search(html):
                return True
        return False

    @staticmethod
    def _detect_cms(html: str) -> str | None:
        """
        Detecta o CMS ou construtor de sites usado, por fingerprinting
        de strings características no HTML.

        Returns:
            Nome do CMS detectado (ex: "WordPress", "Wix") ou None.
        """
        html_lower = html.lower()
        for cms_name, pattern in _CMS_FINGERPRINTS:
            if re.search(pattern, html_lower):
                return cms_name
        return None

    @staticmethod
    def _get_title(soup: BeautifulSoup) -> str | None:
        """Retorna o texto da tag <title> da página (máx. 200 chars)."""
        tag = soup.find("title")
        if tag:
            return tag.get_text(strip=True)[:200]
        return None


# ---------------------------------------------------------------------------
# Orquestrador principal
# ---------------------------------------------------------------------------

class WebsiteChecker:
    """
    Orquestrador do módulo de verificação de websites.

    Carrega empresas do banco SQLite, executa as verificações
    e persiste os resultados.

    Uso típico:
        checker = WebsiteChecker()
        results = checker.check_all(limit=100)
    """

    def __init__(self, db_path: str = _DB_PATH) -> None:
        self.db = WebsiteDatabase(db_path)

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def check_company(self, company: dict[str, Any]) -> dict[str, Any]:
        """
        Verifica o website de uma empresa e persiste o resultado no banco.

        Args:
            company: Dicionário com pelo menos "id", "name" e "website".
                     Tipicamente um registro da tabela companies.

        Returns:
            Dicionário completo com todos os campos website_* preenchidos.
        """
        company_id = company["id"]
        name = company.get("name", f"id={company_id}")
        raw_url = (company.get("website") or "").strip()

        result = _empty_result(company_id)

        # ── Caso A: Empresa sem website ──────────────────────────────────
        if not raw_url:
            result["website_status"] = "sem_site"
            result["website_score"] = 30
            logger.info(f"[WebsiteChecker] {name!r} → sem_site (+30)")
            self.db.save_result(result)
            return result

        # ── Casos B-F: Verifica a URL ────────────────────────────────────
        logger.info(f"[WebsiteChecker] Verificando: {name!r} → {raw_url[:60]}")

        checker = UrlChecker(raw_url)
        check = checker.check()

        result["website_status"] = check["status"]
        result["website_flags"] = ",".join(check.get("flags", []))
        result["website_mobile"] = (
            1 if check.get("mobile") is True
            else 0 if check.get("mobile") is False
            else None
        )
        result["website_https"] = (
            1 if check.get("https") is True
            else 0 if check.get("https") is False
            else None
        )
        result["website_speed_s"] = check.get("speed_s")
        result["website_score"] = check.get("score", 0)
        result["website_cms"] = check.get("cms")
        result["website_has_contact"] = (
            1 if check.get("has_contact") is True
            else 0 if check.get("has_contact") is False
            else None
        )
        result["website_title"] = check.get("title")
        result["website_checked_at"] = datetime.now().isoformat()

        logger.info(
            f"[WebsiteChecker] ✓ {name!r} → "
            f"status={result['website_status']} | "
            f"score={result['website_score']} | "
            f"flags=[{result['website_flags']}]"
        )

        self.db.save_result(result)
        return result

    def check_all(
        self,
        limit: int = 100,
        delay_between_s: float = 1.5,
    ) -> list[dict[str, Any]]:
        """
        Verifica os websites de todas as empresas pendentes no banco.

        Empresas "pendentes" são aquelas cujo campo website_checked_at
        ainda é NULL (nunca foram verificadas).

        Args:
            limit:            Máximo de empresas a processar nesta execução.
            delay_between_s:  Pausa entre verificações (segundos).

        Returns:
            Lista com os resultados de cada empresa verificada.
        """
        companies = self.db.get_pending(limit=limit)
        total = len(companies)

        if not companies:
            logger.info("[WebsiteChecker] Nenhuma empresa pendente de verificação.")
            return []

        logger.info(
            f"[WebsiteChecker] Iniciando verificação de {total} empresas..."
        )

        results: list[dict[str, Any]] = []
        counters: dict[str, int] = {}

        for idx, company in enumerate(companies, start=1):
            logger.info(
                f"[WebsiteChecker] [{idx}/{total}] {company['name']!r} "
                f"(city={company.get('city')}, niche={company.get('niche')})"
            )

            try:
                result = self.check_company(company)
                results.append(result)

                status = result["website_status"]
                counters[status] = counters.get(status, 0) + 1

            except Exception as exc:
                logger.error(
                    f"[WebsiteChecker] Erro inesperado para "
                    f"{company['name']!r} (id={company['id']}): {exc}"
                )
                # Registra o erro no banco para não reprocessar infinitamente
                error_result = _empty_result(company["id"])
                error_result["website_status"] = "sem_resposta"
                error_result["website_score"] = 20
                self.db.save_result(error_result)

            # Pausa entre verificações (exceto na última)
            if idx < total:
                time.sleep(delay_between_s)

        # Resumo final
        logger.info(
            f"\n{'═' * 50}\n"
            f"  WebsiteChecker — Resumo\n"
            f"  Total verificadas : {len(results)}\n"
            + "".join(
                f"  {status:<20}: {count}\n"
                for status, count in sorted(counters.items(), key=lambda x: -x[1])
            )
            + f"{'═' * 50}"
        )

        return results

    def check_by_id(self, company_id: int) -> dict[str, Any] | None:
        """
        Verifica o website de uma empresa específica pelo ID do banco.

        Útil para re-verificar uma empresa já processada ou para testes.

        Args:
            company_id: ID da empresa na tabela companies.

        Returns:
            Resultado da verificação ou None se empresa não encontrada.
        """
        company = self.db.get_by_id(company_id)
        if not company:
            logger.warning(f"[WebsiteChecker] Empresa id={company_id} não encontrada.")
            return None
        return self.check_company(company)

    def print_stats(self) -> None:
        """Imprime estatísticas das verificações realizadas."""
        stats = self.db.get_stats()
        print(f"\n{'═' * 50}")
        print("  WebsiteChecker — Estatísticas do Banco")
        print(f"{'═' * 50}")
        print(f"  Total de empresas : {stats['total_companies']}")
        print(f"  Verificadas       : {stats['checked']}")
        print(f"  Pendentes         : {stats['pending']}")
        if stats["by_status"]:
            print(f"\n  Distribuição por status:")
            for status, count in stats["by_status"].items():
                bar = "█" * min(count, 30)
                print(f"    {status:<22} {bar} {count}")
        print(f"{'═' * 50}\n")


# ---------------------------------------------------------------------------
# Funções de conveniência (API funcional — sem instanciar classe)
# ---------------------------------------------------------------------------

def check_website(url: str) -> dict[str, Any]:
    """
    Verifica um único site e retorna o relatório completo.

    Função de conveniência para uso standalone sem banco de dados.

    Args:
        url: URL do site a verificar (com ou sem protocolo).

    Returns:
        Dicionário com: status, flags, mobile, https, speed_s,
        cms, has_contact, title, score.

    Exemplo:
        result = check_website("exemplo.com.br")
        print(result["status"])   # "ok"
        print(result["score"])    # 15
        print(result["flags"])    # ["nao_mobile", "sem_contato"]
    """
    checker = UrlChecker(url)
    return checker.check()


def is_website_alive(url: str) -> bool:
    """
    Verificação rápida: o site está online e respondendo?

    Args:
        url: URL do site a verificar.

    Returns:
        True se o site respondeu com HTTP 2xx ou 3xx/4xx (está no ar).
        False se timeout, SSL error, connection error, etc.
    """
    result = check_website(url)
    return result["status"] not in ("site_fora", "erro_ssl", "timeout", "sem_resposta")


def get_website_score(url: str) -> int:
    """
    Retorna apenas a pontuação de oportunidade do site (0 a 30+).

    Args:
        url: URL do site a verificar (ou string vazia para "sem site").

    Returns:
        Pontuação inteira. Valores maiores = mais oportunidade de venda.
    """
    if not url or not url.strip():
        return 30   # sem_site = máxima oportunidade
    result = check_website(url)
    return result.get("score", 0)


def detect_cms(url: str) -> str | None:
    """
    Detecta o CMS ou construtor de sites usado.

    Args:
        url: URL do site a verificar.

    Returns:
        Nome do CMS (ex: "WordPress", "Wix") ou None se não detectado.
    """
    try:
        session = requests.Session()
        session.headers.update(_HEADERS)
        response = session.get(url, timeout=_HTTP_TIMEOUT, verify=False)  # noqa: S501
        return UrlChecker._detect_cms(response.text)
    except Exception:
        return None


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

    # ── Modo 1: verifica uma URL passada como argumento ──────────────────
    if len(sys.argv) > 1:
        test_url = sys.argv[1]
        print(f"\nVerificando: {test_url}\n{'─' * 50}")
        result = check_website(test_url)
        for key, val in result.items():
            icon = {
                "status": "📋",
                "flags": "🚩",
                "mobile": "📱",
                "https": "🔒",
                "speed_s": "⚡",
                "cms": "🔧",
                "has_contact": "📞",
                "title": "📄",
                "score": "🎯",
            }.get(key, "  ")
            print(f"  {icon} {key:<16}: {val}")
        print()

    # ── Modo 2: processa empresas do banco ───────────────────────────────
    else:
        print(f"\n{'═' * 60}")
        print("  Website Checker — Processando empresas do banco")
        print(f"{'═' * 60}\n")

        try:
            checker = WebsiteChecker()
            checker.print_stats()

            results = checker.check_all(limit=20)

            print(f"\n{'─' * 60}")
            print(f"  Verificadas nesta sessão: {len(results)}")
            print(f"{'─' * 60}")

            checker.print_stats()

        except FileNotFoundError as e:
            print(f"\n❌ Erro: {e}")
            print("   Execute primeiro: python src/google_maps.py\n")
