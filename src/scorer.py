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
        "lavanderia",
    }
    # compat aliases antigos
    _NICHES_ALTO = _NICHES_S
    _NICHES_MEDIO = _NICHES_A | _NICHES_B

    @staticmethod
    def _phone_digits(phone: Any) -> str:
        import re
        return re.sub(r"\D", "", str(phone or ""))

    def calculate_score(self, company: dict[str, Any]) -> dict[str, Any]:
        """
        Score de OPORTUNIDADE DE VENDA — máximo 100.

        Blocos:
            A) Dor digital ........ 0–35  (sem site / social solta / site ruim)
            B) Capacidade pagar ... 0–30  (nicho S/A/B/C + porte por reviews)
            C) Visibilidade ....... 0–20  (nota Maps + volume + IG)
            D) Abordabilidade ..... 0–15  (celular/fixo; sem fone = teto 55)

        Cores UI: 90+ ouro · 71–89 roxo · 51–70 verde · 0–50 cinza
        """
        score = 0
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

        # ── A. Dor digital (0–35) ────────────────────────────────────────
        if no_website:
            score += 28
            problems.append("Sem site próprio (+28) — principal gatilho de venda")
            services.append("Site profissional")
            services.append("Google Meu Negócio + presença local")
            if only_social:
                score += 4
                problems.append("Só rede social no lugar do site (+4)")
        else:
            tech = 0
            if "sem_https" in web_flags or company.get("website_https") == 0:
                tech += 6
                problems.append("Site sem HTTPS (+6)")
                services.append("Certificado SSL")
            speed = company.get("website_speed_s")
            try:
                if speed is not None and float(speed) > 5.0:
                    tech += 6
                    problems.append(f"Site lento ({float(speed):.1f}s) (+6)")
                    services.append("Otimização de velocidade")
            except (TypeError, ValueError):
                pass
            if "nao_mobile" in web_flags or company.get("website_mobile") == 0:
                tech += 6
                problems.append("Site não mobile-friendly (+6)")
                services.append("Design responsivo")
            score += min(18, tech)

        has_ig = (
            company.get("instagram_status") == "tem_instagram"
            or bool(company.get("instagram_url"))
            or bool(company.get("instagram_username"))
        )
        if no_website and has_ig:
            score += 3
            problems.append("Tem Instagram sem site (+3) — já se expõe, falta casa na web")
            services.append("Site + link na bio")
            if company.get("instagram_has_link") == 0:
                score += 2
                problems.append("Instagram sem link na bio (+2)")

        # ── B. Capacidade de pagar / nicho (0–30) ────────────────────────
        niche = (company.get("niche") or "").strip().lower()
        if niche in self._NICHES_S:
            score += 24
            problems.append(f"Nicho S — alta propensão/ticket ({niche}) (+24)")
            services.append("Site + agenda / captura de leads")
        elif niche in self._NICHES_A:
            score += 16
            problems.append(f"Nicho A — boa propensão ({niche}) (+16)")
            services.append("Site institucional + captura")
        elif niche in self._NICHES_B:
            score += 10
            problems.append(f"Nicho B — ticket menor ({niche}) (+10)")
            services.append("Site + WhatsApp")
        elif niche in self._NICHES_C:
            score += 4
            problems.append(f"Nicho C — baixa prioridade site ({niche}) (+4)")
        elif niche:
            score += 6
            problems.append(f"Nicho local ({niche}) (+6)")
        else:
            score += 3

        try:
            rating = float(company.get("rating") or 0)
        except (TypeError, ValueError):
            rating = 0.0
        try:
            reviews = int(company.get("review_count") or 0)
        except (TypeError, ValueError):
            reviews = 0

        # porte / condição inferida por volume de reviews
        if 20 <= reviews <= 80:
            score += 5
            problems.append(f"Porte saudável ({reviews} avaliações) (+5)")
        elif 8 <= reviews <= 19:
            score += 3
            problems.append(f"Porte em crescimento ({reviews} avaliações) (+3)")
        elif reviews >= 81:
            score += 2
            problems.append(f"Muita tração no Maps ({reviews} avaliações) (+2)")
        elif reviews <= 1:
            score -= 2
            problems.append("Pouca prova social no Maps (−2)")

        # ── C. Visibilidade / tração real (0–20) ─────────────────────────
        vis = 0
        if rating >= 4.5 and reviews >= 5:
            vis += 10
            problems.append(f"Nota excelente ({rating:.1f}) + reviews (+10)")
        elif rating >= 4.0 and reviews >= 3:
            vis += 7
            problems.append(f"Boa reputação Maps ({rating:.1f}) (+7)")
        elif rating >= 3.5:
            vis += 4
            problems.append(f"Reputação ok ({rating:.1f}) (+4)")
        elif rating > 0 and rating < 3.0 and reviews >= 5:
            vis += 1
            problems.append(f"Nota baixa ({rating:.1f}) (+1)")

        if 5 <= reviews <= 40:
            vis += 5
            problems.append(f"Volume ideal de avaliações ({reviews}) (+5)")
        elif 41 <= reviews <= 100:
            vis += 3
            problems.append(f"Volume alto de avaliações ({reviews}) (+3)")
        elif reviews > 100:
            vis += 2
            problems.append(f"Muitas avaliações ({reviews}) (+2) — pode achar que não precisa")

        if no_website and has_ig:
            vis += 3
            # já contou IG na dor; aqui reforça tração sem estourar muito
            problems.append("Presença social ativa (+3 visibilidade)")

        score += min(20, vis)

        # ── D. Abordabilidade (0–15) ─────────────────────────────────────
        digits = self._phone_digits(company.get("phone"))
        has_mobile = len(digits) >= 12 or (len(digits) >= 11 and digits.startswith("55"))
        has_landline = len(digits) >= 10 and not has_mobile
        if has_mobile:
            score += 15
            problems.append("Telefone celular / WhatsApp (+15)")
            services.append("Abordagem por WhatsApp")
        elif has_landline:
            score += 9
            problems.append("Telefone fixo utilizável (+9)")
            services.append("Ligação comercial")
        else:
            problems.append("Sem telefone útil (+0) — não entra no topo quente")

        # ── Cap 0–100 + teto sem telefone ────────────────────────────────
        score = max(0, min(100, int(score)))
        if not has_mobile and not has_landline and score > 55:
            problems.append(f"Teto 55 sem telefone (era {score})")
            score = 55

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

    def score_one(self, company_id: int) -> dict[str, Any] | None:
        """
        Pontua UMA empresa na hora (pra aparecer no dashboard sem esperar o lote).

        Usado assim que o Maps acha um lead sem site — se o bot parar no meio,
        o que já entrou já está classificado como Raio.
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
        )
        # Distribui Raio pro cliente com cota disponível
        if result.get("lead_class") == "raio":
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
                cur.execute(
                    """
                    SELECT * FROM companies
                    WHERE (business_status IS NULL OR business_status != 'CLOSED_PERMANENTLY')
                      AND (
                          website_status IN ('sem_site', 'so_social')
                          OR website IS NULL
                          OR TRIM(COALESCE(website, '')) = ''
                          OR lead_class = 'raio'
                          OR website ILIKE '%%instagram.com%%'
                          OR website ILIKE '%%facebook.com%%'
                          OR website ILIKE '%%linktr.ee%%'
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
            logger.info("[LeadScorer] Nenhuma empresa para pontuar.")
            return []

        logger.info(
            f"[LeadScorer] Qualificando {len(companies)} empresas "
            f"({'re-score 0-100' if force else 'pendentes'})..."
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
