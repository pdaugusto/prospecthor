"""
menu_checker.py — Verificação de cardápio digital
===================================================

Identifica se estabelecimentos do setor de alimentação possuem cardápio digital
acessível para os clientes (no Google Maps, Instagram, apps de delivery ou site próprio).

Categorias alvo:
    Restaurante, Bar, Lanchonete, Cafeteria, Pizzaria, Hamburgueria, Padaria,
    Sushi, Churrascaria e similares.

Critérios de classificação:
    - sem_cardapio  → +20 pts   Nenhum canal digital com cardápio disponível
    - desatualizado → +15 pts   Indício de cardápio antigo (ex: link de PDF quebrado/antigo)
    - incompleto    → +10 pts   Cardápio parcial (ex: sem preços ou fotos)
    - so_apps       → +10 pts   Disponível apenas em apps de delivery de terceiros
    - ok            → +0 pts    Cardápio próprio e estruturado online

Uso:
    from src.menu_checker import MenuChecker

    checker = MenuChecker()
    results = checker.check_all(limit=50)
"""

from __future__ import annotations

import json
import os
import re
import psycopg2
import psycopg2.extras
import time
import random
import urllib.parse
from datetime import datetime
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

_DATABASE_URL: str = os.getenv("DATABASE_URL", "")
_DB_PATH: str = os.getenv("DATABASE_PATH", "data/leads.db")
_HEADLESS: bool = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
_TIMEOUT_MS: int = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "30000"))

# Lista de palavras-chave de categorias alimentícias no banco
_FOOD_CATEGORIES = [
    "restaurante", "bar", "lanchonete", "cafeteria", "pizzaria",
    "hamburgueria", "padaria", "sushi", "churrascaria", "comida",
    "gastronomia", "doceria", "sorveteria", "bistrô", "rotisseria",
    "adega", "choperia", "pub", "pastéis", "espetinho", "café"
]

# Padrões de URLs de aplicativos de delivery comuns
_APP_PATTERNS = {
    "ifood": re.compile(r"ifood\.com\.br/delivery/"),
    "rappi": re.compile(r"rappi\.com\.br/restaurantes/|rappi\.com/"),
    "99food": re.compile(r"food\.99app\.com/|99food"),
    "aiqfome": re.compile(r"aiqfome\.com/"),
    "goomer": re.compile(r"goomer\.app/|goomer\.com\.br"),
    "tonolucro": re.compile(r"tonolucro\.com\.br"),
    "deliverydireto": re.compile(r"deliverydireto\.com\.br/")
}

# ---------------------------------------------------------------------------
# Colunas de migração do banco
# ---------------------------------------------------------------------------

_MENU_COLUMNS: list[tuple[str, str]] = [
    ("menu_google",      "INTEGER"),  # 0/1
    ("menu_instagram",   "INTEGER"),  # 0/1
    ("menu_apps",        "TEXT"),     # JSON string com links de delivery
    ("menu_site",        "INTEGER"),  # 0/1
    ("menu_status",      "TEXT"),     # sem|so_apps|desatualizado|incompleto|ok
    ("menu_score",       "INTEGER"),
    ("menu_checked_at",  "TEXT"),
]

_SAVE_MENU_SQL = """
UPDATE companies SET
    menu_google      = %(menu_google)s,
    menu_instagram   = %(menu_instagram)s,
    menu_apps        = %(menu_apps)s,
    menu_site        = %(menu_site)s,
    menu_status      = %(menu_status)s,
    menu_score       = %(menu_score)s,
    menu_checked_at  = %(menu_checked_at)s
WHERE id = %(id)s;
"""

# Seleciona empresas de alimentação pendentes
_SELECT_PENDING_SQL = """
SELECT id, name, website, city, state, niche, category, instagram_username, instagram_bio, maps_url
FROM companies
WHERE menu_checked_at IS NULL
  AND (business_status IS NULL OR business_status != 'CLOSED_PERMANENTLY')
ORDER BY id
LIMIT %(limit)s;
"""

_SELECT_BY_ID_SQL = """
SELECT id, name, website, city, state, niche, category, instagram_username, instagram_bio, maps_url
FROM companies WHERE id = %s;
"""


# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------

class MenuDatabase:
    """
    Gerencia a persistência de dados de verificação de cardápios no PostgreSQL.
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
        """Adiciona as colunas de cardápio na tabela de empresas se necessário."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            added = []
            for col_name, col_type in _MENU_COLUMNS:
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
                        logger.warning(f"[DB] Não adicionou coluna {col_name!r}: {exc}")
            if added:
                conn.commit()
                logger.info(f"[DB] Migração Cardápio: {len(added)} colunas → {added}")
            cur.close()
        finally:
            conn.close()

    def get_pending(self, limit: int = 100) -> list[dict[str, Any]]:
        """
        Retorna empresas do nicho de alimentação pendentes de verificação de cardápio.
        """
        conn = self._connect()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(_SELECT_PENDING_SQL, {"limit": limit * 3})
            companies = cur.fetchall()
            cur.close()
            
            # Filtra apenas empresas do nicho de alimentação
            filtered = []
            for c in companies:
                cat = (c.get("category") or "").lower()
                niche = (c.get("niche") or "").lower()
                name = (c.get("name") or "").lower()
                
                # Verifica se alguma palavra-chave de comida bate com a categoria, nicho ou nome
                if any(x in cat or x in niche or x in name for x in _FOOD_CATEGORIES):
                    filtered.append(dict(c))
                else:
                    # Se não for de alimentação, marca como 'nao_aplicavel' para não processar mais
                    self.mark_non_food(c["id"])
                    
                if len(filtered) >= limit:
                    break
            return filtered
        finally:
            conn.close()

    def mark_non_food(self, company_id: int) -> None:
        """Marca empresas que não são do nicho de alimentação de forma amigável."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE companies SET menu_status = 'nao_aplicavel', menu_score = 0, "
                "menu_checked_at = %s WHERE id = %s",
                (datetime.now().isoformat(), company_id)
            )
            conn.commit()
            cur.close()
        except Exception:
            pass
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
        """Salva a qualificação do cardápio digital."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(_SAVE_MENU_SQL, result)
            conn.commit()
            cur.close()
            logger.debug(
                f"[DB] Cardápio salvo: status={result['menu_status']!r} "
                f"score={result['menu_score']} id={result['id']}"
            )
        except Exception as exc:
            logger.error(f"[DB] Erro ao salvar cardápio id={result.get('id')}: {exc}")
        finally:
            conn.close()

    def get_stats(self) -> dict[str, Any]:
        """Retorna estatísticas do checker de cardápio."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM companies WHERE menu_status != 'nao_aplicavel' OR menu_status IS NULL"
            )
            total_food = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM companies WHERE menu_checked_at IS NOT NULL AND menu_status != 'nao_aplicavel'"
            )
            checked = cur.fetchone()[0]
            cur.execute(
                "SELECT menu_status, COUNT(*) as cnt FROM companies "
                "WHERE menu_checked_at IS NOT NULL AND menu_status != 'nao_aplicavel' "
                "GROUP BY menu_status ORDER BY cnt DESC;"
            )
            by_status = cur.fetchall()
            cur.close()
            return {
                "total_food": total_food,
                "checked": checked,
                "pending": total_food - checked,
                "by_status": {row[0]: row[1] for row in by_status},
            }
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Resultado padrão
# ---------------------------------------------------------------------------

def _empty_result(company_id: int) -> dict[str, Any]:
    """Cria um dicionário de resultado zerado."""
    return {
        "id": company_id,
        "menu_google": 0,
        "menu_instagram": 0,
        "menu_apps": "[]",
        "menu_site": 0,
        "menu_status": "sem_cardapio",
        "menu_score": 20,
        "menu_checked_at": datetime.now().isoformat(),
    }


# ---------------------------------------------------------------------------
# Verificação de cardápio próprio no Site
# ---------------------------------------------------------------------------

def _check_site_for_menu(website_url: str) -> bool:
    """
    Varre o website da empresa à procura de links/páginas de cardápio.

    Pesquisa por links no menu principal contendo palavras como:
    /cardapio, /menu, /pdf, /pedidos, /delivery, cardapio.pdf.

    Args:
        website_url: URL do site a varrer.

    Returns:
        True se encontrar links estruturados de cardápio, False caso contrário.
    """
    if not website_url or "instagram.com" in website_url or "facebook.com" in website_url:
        return False
        
    if not website_url.startswith(("http://", "https://")):
        website_url = "http://" + website_url

    try:
        response = requests.get(
            website_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            },
            timeout=8,
            allow_redirects=True,
        )
        if response.status_code != 200:
            return False

        soup = BeautifulSoup(response.text, "lxml")
        
        # Palavras-chave indicativas de página de cardápio ou botões de pedido
        keywords = ["cardapio", "cardápio", "menu", "pdf", "delivery", "pedir", "pedido", "comprar"]
        
        for link in soup.find_all("a", href=True):
            href = link.get("href", "").lower()
            text = link.get_text().lower()
            
            if any(k in href or k in text for k in keywords):
                # Filtra links externos que apontam para redes sociais normais
                if not any(soc in href for soc in ["facebook.com", "instagram.com", "youtube.com"]):
                    logger.debug(f"[Menu Site] Link encontrado: href={href} text={text}")
                    return True
        return False
    except Exception as exc:
        logger.debug(f"[Menu Site] Falha ao verificar site {website_url}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Verificação do Cardápio no Instagram (Bio / Destaques)
# ---------------------------------------------------------------------------

def _check_instagram_for_menu(instagram_bio: str | None, username: str | None) -> bool:
    """
    Analisa a bio do Instagram da empresa e busca indícios de cardápio digital.

    Verifica se a bio possui menções a "cardápio", "menu", "peça por aqui"
    ou links contendo "linktr.ee", "goomer", "menudino", "wa.me".

    Args:
        instagram_bio: Bio coletada anteriormente.
        username:      Username do Instagram da empresa.

    Returns:
        True se encontrar pistas de cardápio, False caso contrário.
    """
    if not instagram_bio:
        return False

    bio_lower = instagram_bio.lower()
    
    # Palavras indicadoras de cardápio/links úteis de vendas
    keywords = ["cardapio", "cardápio", "menu", "delivery", "pedidos", "peça", "linktr.ee", "goomer", "menudino", "wa.me", "whatsapp"]
    if any(k in bio_lower for k in keywords):
        logger.debug(f"[Menu Instagram] Bio de @{username} contém palavras-chave de cardápio.")
        return True

    return False


# ---------------------------------------------------------------------------
# Verificação do Cardápio no Google Maps (Ficha local)
# ---------------------------------------------------------------------------

class GoogleMapsMenuChecker:
    """
    Varre a ficha do Google Maps usando Playwright para identificar se a empresa
    possui um link oficial de cardápio cadastrado na ficha do Google.
    """

    def __init__(self, page: Page) -> None:
        self.page = page

    def check(self, maps_url: str) -> bool:
        """
        Navega para a URL da ficha do Maps e procura pelo botão/link de cardápio.

        Seletor analisado: links com atributo [data-item-id="menu"]
        ou aria-label que contenha "Cardápio".

        Args:
            maps_url: URL da ficha no Google Maps.

        Returns:
            True se link ou botão de cardápio for encontrado.
        """
        if not maps_url:
            return False

        try:
            self.page.goto(maps_url, wait_until="domcontentloaded", timeout=15000)
            
            # Aguarda o painel carregar
            self.page.wait_for_timeout(1000)
            
            # Busca botões que possuem link de cardápio
            menu_btn = self.page.locator(
                "a[data-item-id='menu'], [aria-label*='Cardápio'], [aria-label*='Menu']"
            ).first
            
            if menu_btn.is_visible(timeout=3000):
                logger.debug("[Menu Google] Botão/link de cardápio visível no Google Maps.")
                return True
                
            # Fallback: varre links na página que contenham palavras-chave
            links = self.page.locator("a[href]").all()
            for link in links:
                try:
                    href = link.get_attribute("href") or ""
                    aria = link.get_attribute("aria-label") or ""
                    if "menu" in href.lower() or "cardapio" in href.lower() or "cardapio" in aria.lower():
                        logger.debug(f"[Menu Google] Link alternativo encontrado na página: {href}")
                        return True
                except Exception:
                    continue
                    
            return False
        except Exception as exc:
            logger.debug(f"[Menu Google] Falha ao verificar ficha do Maps: {exc}")
            return False


# ---------------------------------------------------------------------------
# Verificação de presença em Apps de Delivery (iFood, Rappi, 99Food)
# ---------------------------------------------------------------------------

class DeliveryAppsChecker:
    """
    Busca links em motores de busca públicos para determinar a presença da empresa
    em aplicativos de entrega rápida (iFood, Rappi, etc.).
    """

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })

    def search_apps(self, company_name: str, city: str) -> list[str]:
        """
        Faz uma busca no DuckDuckGo (HTML) por plataformas de delivery associadas
        ao nome do restaurante.

        Args:
            company_name: Nome do restaurante.
            city:         Cidade para filtrar o resultado.

        Returns:
            Lista de links para iFood, Rappi, 99Food ou Goomer encontrados.
        """
        query = f"{company_name} {city} delivery"
        url = "https://html.duckduckgo.com/html/"
        
        found_links = []
        try:
            response = self._session.get(url, params={"q": query}, timeout=8)
            if response.status_code != 200:
                return []

            soup = BeautifulSoup(response.text, "lxml")
            
            # Varre todos os links dos resultados de busca
            for a in soup.find_all("a", class_="result__url", href=True):
                href = urllib.parse.unquote(a["href"])
                
                # Verifica se a URL bate com algum app conhecido
                for app_name, pattern in _APP_PATTERNS.items():
                    if pattern.search(href):
                        # Limpa links de redirect do DDG
                        m = re.search(r"uddg=(https?://[^&]+)", href)
                        clean_url = m.group(1) if m else href
                        
                        if clean_url not in found_links:
                            found_links.append(clean_url)
                            logger.debug(f"[Menu Apps] Encontrado canal {app_name.upper()}: {clean_url}")
            
            return found_links
        except Exception as exc:
            logger.debug(f"[Menu Apps] Erro na busca de aplicativos para {company_name}: {exc}")
            return []


# ---------------------------------------------------------------------------
# Orquestrador Principal
# ---------------------------------------------------------------------------

class MenuChecker:
    """
    Orquestrador para o módulo de verificação de cardápio digital.

    Itera sobre estabelecimentos no banco SQLite, verifica a presença
    de cardápio em múltiplos canais e gera o score.
    """

    def __init__(self, db_path: str = _DB_PATH) -> None:
        self.db = MenuDatabase(db_path)
        self._apps_checker = DeliveryAppsChecker()

    def check_company(self, company: dict[str, Any], page: Page | None = None) -> dict[str, Any]:
        """
        Qualifica e classifica os canais de cardápio digital de um estabelecimento.

        Args:
            company: Registro do banco de dados contendo dados da empresa.
            page:    Instância do Playwright Page ativo (opcional).

        Returns:
            Dicionário com o resultado final da verificação para persistência.
        """
        company_id = company["id"]
        name = company.get("name", "")
        city = company.get("city", "")
        website = company.get("website", "") or ""
        instagram_username = company.get("instagram_username")
        instagram_bio = company.get("instagram_bio")
        maps_url = company.get("maps_url") or ""

        result = _empty_result(company_id)
        
        # 1. Verifica no Site
        menu_site = 1 if _check_site_for_menu(website) else 0
        result["menu_site"] = menu_site

        # 2. Verifica no Instagram
        menu_instagram = 1 if _check_instagram_for_menu(instagram_bio, instagram_username) else 0
        result["menu_instagram"] = menu_instagram

        # 3. Verifica no Google Maps (Ficha local)
        menu_google = 0
        if page and maps_url:
            maps_checker = GoogleMapsMenuChecker(page)
            menu_google = 1 if maps_checker.check(maps_url) else 0
        result["menu_google"] = menu_google

        # 4. Verifica nos Apps de Delivery
        app_links = self._apps_checker.search_apps(name, city)
        result["menu_apps"] = json.dumps(app_links)
        has_apps = len(app_links) > 0

        # ------------------------------------------------------------------
        # Classificação Final e Pontuação
        # ------------------------------------------------------------------
        has_direct_menu = (menu_site == 1 or menu_instagram == 1 or menu_google == 1)

        if not has_direct_menu and not has_apps:
            # Caso A: Sem nenhum cardápio disponível
            result["menu_status"] = "sem_cardapio"
            result["menu_score"] = 20
        elif not has_direct_menu and has_apps:
            # Caso B: Tem apenas em iFood, Rappi, etc. (Falta cardápio próprio)
            result["menu_status"] = "so_apps"
            result["menu_score"] = 10
        else:
            # Tem cardápio direto. Vamos inferir se é desatualizado ou incompleto por amostragem
            # ou classificar como OK.
            # Daremos classificação 'ok' por padrão caso possua site ou instagram sinalizado
            result["menu_status"] = "ok"
            result["menu_score"] = 0

            # Caso tenhamos apenas sinalização do instagram sem site e sem google menu,
            # consideramos que o cardápio pode estar 'incompleto' (ex: apenas nos stories)
            if menu_instagram == 1 and menu_site == 0 and menu_google == 0:
                result["menu_status"] = "incompleto"
                result["menu_score"] = 10

        result["menu_checked_at"] = datetime.now().isoformat()
        
        logger.info(
            f"[MenuChecker] {name!r} → status={result['menu_status']} | "
            f"score={result['menu_score']} (site={menu_site}, ig={menu_instagram}, "
            f"google={menu_google}, apps={len(app_links)})"
        )
        
        self.db.save_result(result)
        return result

    def check_all(self, limit: int = 50) -> list[dict[str, Any]]:
        """
        Processa em lote a lista de estabelecimentos pendentes de alimentação.

        Instancia o Playwright dinamicamente de forma integrada.

        Args:
            limit: Quantidade de empresas de alimentação a processar.

        Returns:
            Lista de resultados processados.
        """
        companies = self.db.get_pending(limit=limit)
        total = len(companies)

        if not companies:
            logger.info("[MenuChecker] Nenhuma empresa de alimentação pendente.")
            return []

        logger.info(f"[MenuChecker] Iniciando análise de {total} empresas...")

        results = []
        counters: dict[str, int] = {}

        # Inicializa Playwright para varrer o Google Maps em lote
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=_HEADLESS)
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = context.new_page()

            for idx, company in enumerate(companies, start=1):
                logger.info(
                    f"[MenuChecker] [{idx}/{total}] Restaurante: {company['name']!r} "
                    f"({company.get('city')})"
                )
                try:
                    result = self.check_company(company, page)
                    results.append(result)
                    
                    status = result["menu_status"]
                    counters[status] = counters.get(status, 0) + 1
                except Exception as exc:
                    logger.error(f"[MenuChecker] Falha ao processar {company['name']!r}: {exc}")
                    err_result = _empty_result(company["id"])
                    err_result["menu_status"] = "nao_verificado"
                    self.db.save_result(err_result)
                
                # Delay amigável entre requisições externas
                time.sleep(random.uniform(2.0, 4.0))

            browser.close()

        # Log de resumo
        logger.info(
            f"\n{'═' * 50}\n"
            f"  MenuChecker — Resumo da Sessão\n"
            f"  Total qualificadas: {len(results)}\n"
            + "".join(f"  {k:<20}: {v}\n" for k, v in sorted(counters.items(), key=lambda x: -x[1]))
            + f"{'═' * 50}"
        )
        return results

    def print_stats(self) -> None:
        """Exibe resumo das estatísticas consolidadas no banco."""
        stats = self.db.get_stats()
        print(f"\n{'═' * 55}")
        print("  MenuChecker — Estatísticas do Setor de Alimentação")
        print(f"{'═' * 55}")
        print(f"  Total restaurantes : {stats['total_food']}")
        print(f"  Qualificados       : {stats['checked']}")
        print(f"  Pendentes          : {stats['pending']}")
        if stats["by_status"]:
            print(f"\n  Distribuição por canal de cardápio:")
            for status, count in stats["by_status"].items():
                bar = "█" * min(count, 25)
                print(f"    {status:<20} {bar} {count}")
        print(f"{'═' * 55}\n")


# ---------------------------------------------------------------------------
# Funções de Conveniência (API Stands-alone)
# ---------------------------------------------------------------------------

def check_menu(company_name: str, city: str, website_url: str | None = None) -> dict[str, Any]:
    """
    Função helper para qualificar o cardápio de forma isolada (sem banco de dados).
    """
    results = {
        "menu_site": 0,
        "menu_instagram": 0,
        "menu_google": 0,
        "menu_apps": "[]",
        "menu_status": "sem_cardapio",
        "menu_score": 20
    }

    if website_url and _check_site_for_menu(website_url):
        results["menu_site"] = 1

    app_checker = DeliveryAppsChecker()
    links = app_checker.search_apps(company_name, city)
    results["menu_apps"] = json.dumps(links)

    has_apps = len(links) > 0
    has_site = results["menu_site"] == 1

    if has_site:
        results["menu_status"] = "ok"
        results["menu_score"] = 0
    elif has_apps:
        results["menu_status"] = "so_apps"
        results["menu_score"] = 10

    return results


# ---------------------------------------------------------------------------
# Execução direta (Testes/CLI)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logger.remove()
    logger.add(
        sink=sys.stdout,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        colorize=True,
        level="INFO"
    )

    if len(sys.argv) > 2:
        name = sys.argv[1]
        city = sys.argv[2]
        site = sys.argv[3] if len(sys.argv) > 3 else None
        
        print(f"\nVerificando cardápio de: {name} ({city})")
        res = check_menu(name, city, site)
        print(json.dumps(res, indent=4, ensure_ascii=False))
    else:
        try:
            checker = MenuChecker()
            checker.print_stats()
            checker.check_all(limit=5)
            checker.print_stats()
        except FileNotFoundError as e:
            print(f"\n❌ Erro: {e}")
            print("   Execute primeiro: python src/google_maps.py\n")
