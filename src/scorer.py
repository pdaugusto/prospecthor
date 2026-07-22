"""
scorer.py — Pontuação e qualificação de leads
===============================================

Lê as empresas do banco SQLite com seus respectivos dados de presença digital
já analisados (site, instagram, cardápio, avaliações Google) e calcula a
pontuação total e classificação do lead.

Lógica de qualificação:
    Score ALTO = Presença digital FRACA = Maior oportunidade de venda de serviços.

Uso:
    from src.scorer import LeadScorer

    scorer = LeadScorer()
    # Pontua todas as empresas pendentes ou atualiza todas as existentes
    results = scorer.score_all()
"""

from __future__ import annotations

import json
import os
import psycopg2
import psycopg2.extras
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from loguru import logger

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

load_dotenv()

_DATABASE_URL: str = os.getenv("DATABASE_URL", "")
_DB_PATH: str = os.getenv("DATABASE_PATH", "data/leads.db")  # mantido para compat com main.py

# ---------------------------------------------------------------------------
# Colunas de migração do banco
# ---------------------------------------------------------------------------

_SCORER_COLUMNS: list[tuple[str, str]] = [
    ("lead_score",       "INTEGER"),
    ("lead_class",       "TEXT"),     # raio|trovao|eco
    ("lead_problems",    "TEXT"),     # JSON list de strings
    ("lead_services",    "TEXT"),     # JSON list de sugeridos
    ("lead_priority",    "TEXT"),     # alta|media|baixa|nenhuma
    ("scored_at",        "TEXT"),     # ISO-8601
]

_SAVE_SCORER_SQL = """
UPDATE companies SET
    lead_score    = %(lead_score)s,
    lead_class    = %(lead_class)s,
    lead_problems = %(lead_problems)s,
    lead_services = %(lead_services)s,
    lead_priority = %(lead_priority)s,
    scored_at     = %(scored_at)s
WHERE id = %(id)s;
"""

# Só pontua quem ainda não foi pontuado.
# Inclui: já checados OU sem site (website vazio / social) — mesmo se o bot parou no meio.
_SELECT_ALL_COLLECTED_SQL = """
SELECT * FROM companies
WHERE (business_status IS NULL OR business_status != 'CLOSED_PERMANENTLY')
  AND scored_at IS NULL
  AND (
      website_checked_at IS NOT NULL
      OR website IS NULL
      OR TRIM(COALESCE(website, '')) = ''
      OR website_status IN ('sem_site', 'so_social')
      OR website ILIKE '%%instagram.com%%'
      OR website ILIKE '%%facebook.com%%'
      OR website ILIKE '%%linktr.ee%%'
  )
ORDER BY id;
"""


# ---------------------------------------------------------------------------
# Banco de dados do Scorer
# ---------------------------------------------------------------------------

class ScorerDatabase:
    """
    Gerencia transações no PostgreSQL específicas para o qualificador de leads.
    """

    def __init__(self, db_path: str = _DB_PATH) -> None:
        # db_path mantido na assinatura para compatibilidade
        if not _DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL não configurada. Execute os checkers com DATABASE_URL no .env."
            )
        self._migrate()

    def _connect(self):
        """Retorna uma nova conexão psycopg2."""
        return psycopg2.connect(_DATABASE_URL)

    def _migrate(self) -> None:
        """Adiciona colunas de qualificação à tabela de empresas se necessário."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            added = []
            for col_name, col_type in _SCORER_COLUMNS:
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
                logger.info(f"[DB] Migração Scorer: {len(added)} colunas adicionadas → {added}")
            else:
                logger.debug("[DB] Schema Scorer já atualizado.")
            cur.close()
        finally:
            conn.close()

    def get_collected_companies(self) -> list[dict[str, Any]]:
        """Busca todas as empresas que já passaram por análise de site ou instagram."""
        conn = self._connect()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(_SELECT_ALL_COLLECTED_SQL)
            rows = cur.fetchall()
            cur.close()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_company_by_id(self, company_id: int) -> dict[str, Any] | None:
        """Carrega um registro completo por id."""
        conn = self._connect()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM companies WHERE id = %s LIMIT 1;", (company_id,))
            row = cur.fetchone()
            cur.close()
            return dict(row) if row else None
        finally:
            conn.close()

    def ensure_sem_site_flags(self, company_id: int) -> None:
        """Marca website_status=sem_site para o lead já aparecer no dashboard."""
        now = datetime.now().isoformat()
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE companies SET
                    website_status = COALESCE(NULLIF(website_status, ''), 'sem_site'),
                    website_checked_at = COALESCE(website_checked_at, %s),
                    website_score = COALESCE(website_score, 55)
                WHERE id = %s
                  AND (
                      website IS NULL
                      OR TRIM(COALESCE(website, '')) = ''
                      OR website_status IN ('sem_site', 'so_social')
                      OR website ILIKE '%%instagram.com%%'
                      OR website ILIKE '%%facebook.com%%'
                      OR website ILIKE '%%linktr.ee%%'
                  );
                """,
                (now, company_id),
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def save_lead_score(self, result: dict[str, Any]) -> None:
        """Persiste os resultados da pontuação de lead."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(_SAVE_SCORER_SQL, result)
            conn.commit()
            cur.close()
        except Exception as exc:
            logger.error(f"[DB] Erro ao salvar pontuação de lead id={result.get('id')}: {exc}")
        finally:
            conn.close()

    def get_stats(self) -> dict[str, Any]:
        """Retorna estatísticas dos leads classificados."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM companies WHERE scored_at IS NOT NULL")
            total = cur.fetchone()[0]
            cur.execute(
                "SELECT lead_class, COUNT(*) as cnt FROM companies "
                "WHERE scored_at IS NOT NULL "
                "GROUP BY lead_class ORDER BY cnt DESC;"
            )
            by_class = cur.fetchall()
            cur.close()
        finally:
            conn.close()
        return {
            "total_scored": total,
            "by_class": {row[0]: row[1] for row in by_class},
        }


# ---------------------------------------------------------------------------
# Qualificador de Leads
# ---------------------------------------------------------------------------

class LeadScorer:
    """
    Classifica e calcula a pontuação consolidada de oportunidade de cada lead,
    armazenando o resultado no SQLite.
    """

    def __init__(self, db_path: str = _DB_PATH) -> None:
        self.db = ScorerDatabase(db_path)

    @staticmethod
    def _has_no_website(company: dict[str, Any]) -> bool:
        """
        True quando a empresa não tem site próprio (oportunidade Raio).

        Conta como sem site: URL vazia, status sem_site/so_social, ou
        o campo website apontando só para rede social.
        """
        web_status = (company.get("website_status") or "").strip().lower()
        if web_status in ("sem_site", "so_social"):
            return True

        website = (company.get("website") or "").strip()
        if not website:
            return True

        social_markers = (
            "instagram.com", "facebook.com", "fb.com", "linktr.ee",
            "bio.link", "wa.me", "whatsapp.com", "tiktok.com",
        )
        lower = website.lower()
        return any(m in lower for m in social_markers)

    # Capacidade de pagar + propensão a site (tier S/A/B/C)
    # S: alta propensão + condição | A: boa | B: ticket menor | C: baixa prioridade
    _NICHES_S = {
        "odontologia", "advocacia", "clinica_medica", "clinica", "estetica",
        "contabilidade", "imobiliaria", "arquitetura", "fisioterapia", "psicologia",
        "construtora", "mentores_palestrantes", "seguradora",
    }
    _NICHES_A = {
        "hotel", "escola", "pet", "otica", "joalheria", "academia", "evento",
        "seguranca", "informatica", "marmore_granito",
    }
    _NICHES_B = {
        "salao_barbearia", "restaurante", "acaiteria", "padaria", "comercio",
        "oficina", "eletrica", "limpeza", "construcao", "moveis", "farmacia",
        "fotografia", "manicure", "lava_rapido",
    }
    _NICHES_C = {
        "lavanderia", "foodtruck",
    }
    # Nome/categoria que indicam carrinho/food truck (não restaurante de salão)
    _FOODTRUCK_MARKERS = (
        "food truck", "foodtruck", "food-truck",
        "carrinho", "trailer", "ambulante",
        "hot dog", "hotdog", "cachorro quente", "dogão", "dogao",
        "lanche", "lanches", "lanchinho",
        "pastel ", "pastelaria", "churros",
        "quiosque de", "barraca de",
        "trailer de", "kombi", "food bike", "foodbike",
    )
    # compat aliases antigos
    _NICHES_ALTO = _NICHES_S
    _NICHES_MEDIO = _NICHES_A | _NICHES_B

    @classmethod
    def _looks_like_foodtruck(cls, company: dict[str, Any]) -> bool:
        """True se parece food truck/carrinho/lanche de rua — não restaurante de mesa."""
        niche = (company.get("niche") or "").strip().lower()
        if niche in ("foodtruck", "food_truck"):
            return True
        blob = " ".join(
            str(company.get(k) or "")
            for k in ("name", "category", "address")
        ).lower()
        return any(m in blob for m in cls._FOODTRUCK_MARKERS)

    @staticmethod
    def _phone_digits(phone: Any) -> str:
        import re
        return re.sub(r"\D", "", str(phone or ""))

    @classmethod
    def _phone_kind(cls, phone: Any) -> str:
        """
        celular | fixo | nenhum — regra BR unificada (Maps e Fonte B).
        Celular: DDD + 9 + 8 dígitos (11) ou 55 + isso (12–13).
        """
        d = cls._phone_digits(phone)
        if not d:
            return "nenhum"
        if d.startswith("55") and len(d) >= 12:
            local = d[2:]
        else:
            local = d
        if len(local) >= 11 and local[2:3] == "9":
            return "celular"
        if len(local) >= 10:
            return "fixo"
        return "nenhum"

    @classmethod
    def _services_for_context(
        cls,
        *,
        niche: str,
        no_website: bool,
        has_ig: bool,
        has_mobile: bool,
        is_foodtruck: bool,
    ) -> list[str]:
        """
        Ofertas digitais por nicho — não só “site”.
        Ordem: mais vendável primeiro (máx ~5 itens no final).
        """
        out: list[str] = []

        def add(*items: str) -> None:
            for it in items:
                if it and it not in out:
                    out.append(it)

        # Base digital (quase todo mundo sem site)
        if no_website:
            if is_foodtruck:
                add(
                    "Cardápio digital (QR Code)",
                    "Link na bio + WhatsApp Business",
                    "Página one-page / mini-site de cardápio",
                )
            else:
                add("Site profissional", "Google Meu Negócio otimizado")

        # Por nicho
        n = (niche or "").strip().lower()
        if is_foodtruck or n == "foodtruck":
            add("Cardápio digital", "Pedido via WhatsApp", "Identidade visual do carrinho")
        elif n in ("restaurante", "padaria", "acaiteria"):
            add(
                "Cardápio digital interativo",
                "Integração delivery / iFood",
                "Site com fotos e cardápio",
                "QR Code na mesa",
            )
        elif n in ("odontologia", "clinica_medica", "clinica", "fisioterapia", "psicologia", "estetica"):
            add(
                "Site + agenda online",
                "WhatsApp para marcar consulta",
                "Landing de campanha (Google/Instagram)",
                "Página de depoimentos / antes e depois",
            )
        elif n == "advocacia":
            add(
                "Site institucional com áreas de atuação",
                "Captura de leads (formulário)",
                "Blog / autoridade no Google",
                "Página de contato + WhatsApp",
            )
        elif n == "contabilidade":
            add(
                "Site com serviços e diferenciais",
                "Captura de leads (abertura de empresa)",
                "Material para redes (posts)",
            )
        elif n in ("imobiliaria", "construtora"):
            add(
                "Site com catálogo de imóveis/obras",
                "Landing de lançamento",
                "Tour virtual / galeria",
                "CRM leve + WhatsApp",
            )
        elif n == "marmore_granito":
            add(
                "Catálogo digital de pedras/projetos",
                "Site portfólio com orçamento",
                "Galeria de obras realizadas",
            )
        elif n in ("mentores_palestrantes",):
            add(
                "Site de autoridade + bio",
                "Página de captura (lead magnet)",
                "Agenda / inscrição em eventos",
                "Área de depoimentos e mídia kit",
            )
        elif n in ("salao_barbearia", "manicure"):
            add(
                "Agenda online (horários)",
                "Catálogo de serviços e preços",
                "WhatsApp Business + lembrete",
                "Site one-page + Instagram",
            )
        elif n == "hotel":
            add(
                "Site com reserva / disponibilidade",
                "Galeria e tour",
                "Integração Booking/Airbnb (link)",
            )
        elif n in ("pet",):
            add(
                "Site + serviços (banho/tosa/clínica)",
                "Agendamento online",
                "Cardápio de serviços",
            )
        elif n in ("academia",):
            add(
                "Site com planos e horários",
                "Landing de matrícula",
                "App/link de treinos (opcional)",
            )
        elif n in ("escola",):
            add(
                "Site institucional + matrículas",
                "Área de notícias / blog",
                "Formulário de interesse",
            )
        elif n in ("evento",):
            add(
                "Portfólio digital de eventos",
                "Site one-page + orçamento",
                "Galeria e vídeos",
            )
        elif n in ("oficina", "lava_rapido"):
            add(
                "Página de serviços e preços",
                "Agendamento via WhatsApp",
                "Pacotes / promoções digitais",
            )
        elif n in ("seguradora", "seguranca", "informatica"):
            add(
                "Site B2B com serviços",
                "Landing de cotação",
                "Captação de leads",
            )
        elif n in ("arquitetura", "fotografia", "joalheria", "otica", "moveis"):
            add(
                "Portfólio online",
                "Catálogo de produtos/projetos",
                "Site com galeria profissional",
            )
        elif n in ("comercio", "farmacia", "lavanderia"):
            add(
                "Site institucional / catálogo",
                "WhatsApp para pedidos",
                "Google Meu Negócio reforçado",
            )
        else:
            if no_website:
                add("Site one-page + WhatsApp", "Presença local no Google")

        if has_ig and no_website:
            add("Link na bio + página de destino")
        if has_mobile:
            add("Funil de abordagem por WhatsApp")

        return out[:6]

    def calculate_score(self, company: dict[str, Any]) -> dict[str, Any]:
        """
        Score de OPORTUNIDADE DE VENDA — escala 0–100 real.

        Cada bloco é pontuado de 0 a 100 e depois ponderado (soma = 100%):
            A) Dor digital ........ 32%
            B) Capacidade pagar ... 30%
            C) Visibilidade ....... 23%
            D) Abordabilidade ..... 15%

        Assim o topo (sem site + nicho S + nota boa + celular) chega perto de 100.
        Cores UI: 90+ ouro · 71–89 roxo · 51–70 verde · 0–50 cinza
        """
        problems: list[str] = []
        services: list[str] = []
        no_website = self._has_no_website(company)
        web_flags = [f.strip() for f in (company.get("website_flags") or "").split(",") if f.strip()]
        website = (company.get("website") or "").strip().lower()
        only_social = bool(website) and any(
            m in website
            for m in (
                "instagram.com", "facebook.com", "fb.com", "linktr.ee",
                "bio.link", "wa.me", "whatsapp.com", "tiktok.com",
            )
        )
        has_ig = (
            company.get("instagram_status") == "tem_instagram"
            or bool(company.get("instagram_url"))
            or bool(company.get("instagram_username"))
        )
        try:
            rating = float(company.get("rating") or 0)
        except (TypeError, ValueError):
            rating = 0.0
        try:
            reviews = int(company.get("review_count") or 0)
        except (TypeError, ValueError):
            reviews = 0

        phone_kind = self._phone_kind(company.get("phone"))
        has_mobile = phone_kind == "celular"
        has_landline = phone_kind == "fixo"
        # Instagram conta como canal de contato (Fonte B e Maps)
        has_ig_contact = has_ig

        # ── A) Dor digital 0–100 ─────────────────────────────────────────
        if no_website:
            dor = 88  # base forte: produto = vender presença digital
            problems.append("Sem site próprio (dor alta) — principal gatilho de venda")
            if only_social:
                dor = min(100, dor + 6)
                problems.append("Só rede social no lugar do site")
            if has_ig:
                dor = min(100, dor + 4)
                problems.append("Tem Instagram sem site — já se expõe, falta casa na web")
                if company.get("instagram_has_link") == 0:
                    dor = min(100, dor + 2)
                    problems.append("Instagram sem link na bio")
        else:
            dor = 18  # tem site = dor baixa de “vender site do zero”
            tech = 0
            if "sem_https" in web_flags or company.get("website_https") == 0:
                tech += 22
                problems.append("Site sem HTTPS")
            speed = company.get("website_speed_s")
            try:
                if speed is not None and float(speed) > 5.0:
                    tech += 20
                    problems.append(f"Site lento ({float(speed):.1f}s)")
            except (TypeError, ValueError):
                pass
            if "nao_mobile" in web_flags or company.get("website_mobile") == 0:
                tech += 22
                problems.append("Site não mobile-friendly")
            dor = min(70, dor + tech)

        # ── B) Capacidade / nicho 0–100 ──────────────────────────────────
        niche = (company.get("niche") or "").strip().lower()
        is_foodtruck = self._looks_like_foodtruck(company)
        # "Lanche"/carrinho classificado como restaurante → trata como food truck
        if is_foodtruck:
            cap = 12
            problems.append(
                "Parece food truck/carrinho/lanche — ticket baixo de site "
                "(não é restaurante de salão)"
            )
        elif niche in self._NICHES_S:
            cap = 92
            problems.append(f"Nicho S — alta propensão/ticket ({niche})")
        elif niche in self._NICHES_A:
            cap = 72
            problems.append(f"Nicho A — boa propensão ({niche})")
        elif niche in self._NICHES_B:
            cap = 48
            problems.append(f"Nicho B — ticket menor ({niche})")
        elif niche in self._NICHES_C:
            cap = 28
            problems.append(f"Nicho C — baixa prioridade site ({niche})")
        elif niche:
            cap = 40
            problems.append(f"Nicho local ({niche})")
        else:
            cap = 30

        # porte (ajuste fino ±8) — só quando TEM dado do Google Maps
        # (Fonte B/OSM sem nota não deve cair todo mundo no mesmo teto “pouca prova social”)
        src = (company.get("source") or "").strip().lower()
        maps_data_missing = (rating <= 0 and reviews <= 0)
        partial_source = src in ("osm", "cnpj", "cnpj+osm", "fonte_b", "fonte-b")

        if not maps_data_missing:
            if 12 <= reviews <= 60:
                cap = min(100, cap + 8)
                problems.append(f"Porte saudável ({reviews} avaliações)")
            elif 5 <= reviews <= 11:
                cap = min(100, cap + 4)
                problems.append(f"Porte em crescimento ({reviews} avaliações)")
            elif reviews >= 61:
                cap = min(100, cap + 2)
                problems.append(f"Muita tração no Maps ({reviews} avaliações)")
            elif reviews <= 1:
                cap = max(0, cap - 8)
                problems.append("Pouca prova social no Maps")
        elif partial_source:
            # mesma fórmula de blocos; sem punir por “0 reviews” se nunca teve Maps
            problems.append("Sem nota/avaliações no Google ainda")

        # ── C) Visibilidade 0–100 ────────────────────────────────────────
        # nota Maps (0–55) + volume (0–35) + social (0–10)
        if maps_data_missing and partial_source:
            # neutro (não “nota fraca 5”) — evita todos com 56 por falta de Google
            r_pts = 30
            v_pts = 12
            problems.append("Visibilidade parcial (sem Google) — base neutra")
        elif rating >= 4.7 and reviews >= 3:
            r_pts = 55
            problems.append(f"Nota excelente ({rating:.1f})")
        elif rating >= 4.3 and reviews >= 2:
            r_pts = 48
            problems.append(f"Nota alta ({rating:.1f})")
        elif rating >= 4.0:
            r_pts = 40
            problems.append(f"Boa reputação Maps ({rating:.1f})")
        elif rating >= 3.5:
            r_pts = 28
            problems.append(f"Reputação ok ({rating:.1f})")
        elif rating > 0:
            r_pts = 12
            problems.append(f"Nota fraca ({rating:.1f})")
        else:
            r_pts = 5

        if not (maps_data_missing and partial_source):
            if reviews >= 15:
                v_pts = 35
                problems.append(f"Volume forte de avaliações ({reviews})")
            elif reviews >= 8:
                v_pts = 30
                problems.append(f"Volume bom de avaliações ({reviews})")
            elif reviews >= 3:
                v_pts = 22
                problems.append(f"Algumas avaliações ({reviews})")
            elif reviews >= 1:
                v_pts = 10
            else:
                v_pts = 0

        s_pts = 10 if (no_website and has_ig) else (4 if has_ig else 0)
        if s_pts >= 10:
            problems.append("Presença social (IG) ativa")
        vis = min(100, r_pts + v_pts + s_pts)

        # ── D) Abordabilidade 0–100 ──────────────────────────────────────
        if has_mobile:
            ab = 100
            problems.append("Telefone celular / WhatsApp")
        elif has_landline:
            ab = 65
            problems.append("Telefone fixo utilizável")
        elif has_ig_contact:
            ab = 55
            problems.append("Contato via Instagram (sem telefone)")
        else:
            ab = 0
            problems.append("Sem telefone útil — baixa chance de fechar agora")

        # Serviços sugeridos por nicho (cardápio, agenda, etc. — não só “site”)
        services = self._services_for_context(
            niche=niche,
            no_website=no_website,
            has_ig=has_ig,
            has_mobile=has_mobile,
            is_foodtruck=is_foodtruck,
        )
        if not no_website:
            if "sem_https" in web_flags or company.get("website_https") == 0:
                services.insert(0, "Certificado SSL / HTTPS")
            if "nao_mobile" in web_flags or company.get("website_mobile") == 0:
                services.insert(0, "Site responsivo (mobile)")

        # ── Combinação ponderada → 0–100 ─────────────────────────────────
        # pesos somam 1.0; escala cheia usada de ponta a ponta
        w_dor, w_cap, w_vis, w_ab = 0.32, 0.30, 0.23, 0.15
        raw = (
            w_dor * dor
            + w_cap * cap
            + w_vis * vis
            + w_ab * ab
        )
        score = int(round(max(0.0, min(100.0, raw))))

        # NÃO gravar fórmula de blocos (dor/cap/vis) em lead_problems —
        # isso polui UI, relatórios e "problemas comuns". Score já está em lead_score.

        # sem telefone e sem IG: não fica no topo dourado
        if not has_mobile and not has_landline and not has_ig_contact and score > 58:
            problems.append("Sem telefone/WhatsApp nem Instagram — contato fraco")
            score = 58
        # food truck/carrinho: raramente fecha site "completo" → teto
        if is_foodtruck and score > 62:
            problems.append("Perfil food truck/carrinho — ticket de site costuma ser menor")
            score = 62

        # ── Classificação ────────────────────────────────────────────────
        if no_website:
            lead_class = "raio"
        elif score >= 45:
            lead_class = "trovao"
        else:
            lead_class = "eco"

        if score >= 75:
            priority = "alta"
        elif score >= 55:
            priority = "media"
        else:
            priority = "baixa"

        dedup_services: list[str] = []
        for s in services:
            if s not in dedup_services:
                dedup_services.append(s)

        return {
            "id": company["id"],
            "lead_score": score,
            "lead_class": lead_class,
            "lead_problems": json.dumps(problems, ensure_ascii=False),
            "lead_services": json.dumps(dedup_services, ensure_ascii=False),
            "lead_priority": priority,
            "scored_at": datetime.now().isoformat(),
        }

    def score_one(self, company_id: int, *, assign: bool = True) -> dict[str, Any] | None:
        """
        Pontua UMA empresa na hora (pra aparecer no dashboard sem esperar o lote).

        Usado assim que o Maps acha um lead sem site — se o bot parar no meio,
        o que já entrou já está classificado como Raio.

        assign=False → sobra livre (meta/cota cheia): scoreia mas NÃO atribui a ninguém.
        """
        company = self.db.get_company_by_id(company_id)
        if not company:
            logger.warning(f"[LeadScorer] score_one: id={company_id} não encontrado.")
            return None

        if self._has_no_website(company):
            self.db.ensure_sem_site_flags(company_id)
            company = self.db.get_company_by_id(company_id) or company
            if not company.get("website_status"):
                company["website_status"] = "sem_site"

        result = self.calculate_score(company)
        self.db.save_lead_score(result)
        logger.info(
            f"[LeadScorer] Score imediato id={company_id} "
            f"→ {result['lead_class']} ({result['lead_score']} pts) | "
            f"{company.get('name', '')!r}"
            + (" | SOBRA (sem dono)" if not assign else "")
        )
        # Distribui Raio pro cliente com cota disponível (não em sobras)
        if assign and result.get("lead_class") == "raio":
            try:
                from src.users import assign_raio_lead
                assign_raio_lead(int(company_id))
            except Exception as exc:
                logger.debug(f"[LeadScorer] assign: {exc}")
        return result

    def score_all(self, force: bool = False) -> list[dict[str, Any]]:
        """
        Pontua empresas no banco.

        force=False → só quem ainda não tem scored_at
        force=True  → reprocessa todos os sem site / raio (nova escala 0–100)
        """
        if force:
            conn = self.db._connect()
            try:
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                # force = reprocessa raios / sem site / Fonte B (OSM/CNPJ)
                # (mesmo já tendo scored_at — para corrigir escala antiga)
                cur.execute(
                    """
                    SELECT * FROM companies
                    WHERE (business_status IS NULL OR business_status != 'CLOSED_PERMANENTLY')
                      AND (
                          website_status IN ('sem_site', 'so_social')
                          OR website IS NULL
                          OR TRIM(COALESCE(website, '')) = ''
                          OR lead_class = 'raio'
                          OR lead_class IS NULL
                          OR website ILIKE '%%instagram.com%%'
                          OR website ILIKE '%%facebook.com%%'
                          OR website ILIKE '%%linktr.ee%%'
                          OR lower(COALESCE(source, '')) IN (
                              'osm', 'cnpj', 'cnpj+osm', 'fonte_b', 'fonte-b', 'playwright', 'places_api'
                          )
                          OR lower(COALESCE(source, '')) LIKE '%%osm%%'
                          OR lower(COALESCE(source, '')) LIKE '%%cnpj%%'
                      )
                    ORDER BY id;
                    """
                )
                companies = [dict(r) for r in cur.fetchall()]
                cur.close()
            finally:
                conn.close()
        else:
            companies = self.db.get_collected_companies()

        if not companies:
            logger.info(
                "[LeadScorer] Nenhuma empresa para pontuar"
                + (" (force não achou raios/OSM/CNPJ)." if force else " pendente (já todas com score — use force).")
            )
            return []

        logger.info(
            f"[LeadScorer] Qualificando {len(companies)} empresas "
            f"({'re-score force' if force else 'pendentes'})..."
        )

        scored_leads = []
        raio_count = 0
        for company in companies:
            cid = company.get("id")
            if cid and self._has_no_website(company):
                self.db.ensure_sem_site_flags(int(cid))
                company = self.db.get_company_by_id(int(cid)) or company

            result = self.calculate_score(company)
            self.db.save_lead_score(result)

            company.update(result)
            scored_leads.append(company)
            if result["lead_class"] == "raio":
                raio_count += 1

        scored_leads.sort(key=lambda x: x["lead_score"], reverse=True)

        logger.info(
            f"[LeadScorer] {len(scored_leads)} pontuadas | "
            f"{raio_count} Raio (sem site) | escala 0–100."
        )
        return scored_leads

    def print_resumos(self, limit: int = 10) -> None:
        """
        Gera e exibe um resumo textual dos leads Raio (sem site).
        """
        leads = self.score_all()
        # Dashboard/comercial: apenas empresas sem site
        qualificados = [l for l in leads if l["lead_class"] == "raio"][:limit]

        if not qualificados:
            print("\nNenhum lead ⚡ Raio (sem site) qualificado no momento.\n")
            return

        print(f"\n{'═' * 60}")
        print(f"      ⚡ LEADS RAIO — SEM SITE ({len(qualificados)} principais)")
        print(f"{'═' * 60}\n")

        for lead in qualificados:
            title_tag = "⚡ LEAD RAIO"
            score = lead["lead_score"]
            name = lead.get("name", "Sem Nome")
            address = lead.get("address", "Sem Endereço")
            phone = lead.get("phone") or "Não disponível"
            rating = lead.get("rating") or 0
            reviews = lead.get("review_count") or 0

            problems = json.loads(lead["lead_problems"])
            services = json.loads(lead["lead_services"])
            priority = lead["lead_priority"].upper()

            # Desenha o card do Lead
            print("┌" + "─" * 56 + "┐")
            print(f"│ {title_tag:<22} — Score: {score:<4}/100              │")
            print("│" + " " * 56 + "│")
            print(f"│ 🏪 {name[:50]:<52} │")
            print(f"│ 📍 {address[:50]:<52} │")
            print(f"│ 📞 {phone:<52} │")
            print(f"│ ⭐ {rating} ({reviews} avaliações){' ':<35} │")
            print("│" + " " * 56 + "│")
            print("│ ❌ PROBLEMAS:                                          │")
            for p in problems[:5]:
                print(f"│   → {p:<50} │")
            print("│" + " " * 56 + "│")
            print("│ 💡 SERVIÇOS PRA VENDER:                                │")
            for s in services[:4]:
                print(f"│   → {s:<50} │")
            print("│" + " " * 56 + "│")
            print(f"│ 🎯 PRIORIDADE: {priority:<39} │")
            print("└" + "─" * 56 + "┘")
            print()


# ---------------------------------------------------------------------------
# Execução direta (CLI/Testes)
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

    try:
        scorer = LeadScorer()
        scorer.score_all()
        scorer.print_resumos(limit=10)
    except FileNotFoundError as e:
        print(f"\n❌ Erro: {e}")
        print("   Certifique-se de popular e rodar os checkers antes do scorer.\n")
