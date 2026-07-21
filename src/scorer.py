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

    # Nichos de ticket alto (mais chance de pagar site / serviço digital)
    _NICHES_ALTO = {
        "odontologia", "advocacia", "clinica_medica", "clinica", "estetica",
        "contabilidade", "imobiliaria", "arquitetura", "fisioterapia", "psicologia",
        "construtora",
    }
    _NICHES_MEDIO = {
        "comercio", "restaurante", "pet", "oficina", "academia", "farmacia",
        "padaria", "otica", "moveis", "informatica", "evento", "seguranca",
        "limpeza", "eletrica", "construcao", "hotel", "salao_barbearia", "lavanderia",
        "fotografia", "joalheria", "escola", "acaiteria", "mentores_palestrantes",
    }

    @staticmethod
    def _phone_digits(phone: Any) -> str:
        import re
        return re.sub(r"\D", "", str(phone or ""))

    def calculate_score(self, company: dict[str, Any]) -> dict[str, Any]:
        """
        Score de OPORTUNIDADE DE VENDA — máximo 100.

        Quanto MAIOR o score, MAIOR a chance de fechar (presença digital fraca
        + negócio com potencial + contato utilizável).

        Faixas:
            75–100  quente  (prioridade alta)
            50–74   morno   (prioridade média)
             0–49   frio    (prioridade baixa)

        ⚡ RAIO  = sem site próprio (listado no dashboard)
        ☁️ TROVÃO / 🔊 ECO = tem site (não é o foco principal)
        """
        score = 0
        problems: list[str] = []
        services: list[str] = []
        no_website = self._has_no_website(company)
        web_flags = (company.get("website_flags") or "").split(",")

        # ── 1. Presença digital (núcleo da oportunidade) ─────────────────
        if no_website:
            score += 40
            problems.append("Sem site próprio (+40) — principal gatilho de venda")
            services.append("Site profissional")
            services.append("Google Meu Negócio + presença local")
        else:
            # tem site: oportunidade menor, mas problemas técnicos ainda vendem
            if "sem_https" in web_flags or company.get("website_https") == 0:
                score += 12
                problems.append("Site sem HTTPS (+12)")
                services.append("Certificado SSL")
            speed = company.get("website_speed_s")
            try:
                if speed is not None and float(speed) > 5.0:
                    score += 10
                    problems.append(f"Site lento ({float(speed):.1f}s) (+10)")
                    services.append("Otimização de velocidade")
            except (TypeError, ValueError):
                pass
            if "nao_mobile" in web_flags or company.get("website_mobile") == 0:
                score += 12
                problems.append("Site não mobile-friendly (+12)")
                services.append("Design responsivo")

        # ── 2. Contato (sem telefone = quase não vende) ───────────────────
        digits = self._phone_digits(company.get("phone"))
        if len(digits) >= 12 or (len(digits) >= 11 and digits.startswith("55")):
            # celular BR típico
            score += 20
            problems.append("Telefone celular / WhatsApp (+20)")
            services.append("Abordagem por WhatsApp")
        elif len(digits) >= 10:
            score += 12
            problems.append("Telefone fixo utilizável (+12)")
            services.append("Ligação comercial")
        else:
            problems.append("Sem telefone útil (+0) — baixa chance de fechar")
            # penaliza oportunidade (cap implícito: sem contato não sobe)

        # ── 3. Prova social no Maps (negócio real + vitrine fraca) ───────
        try:
            rating = float(company.get("rating") or 0)
        except (TypeError, ValueError):
            rating = 0.0
        try:
            reviews = int(company.get("review_count") or 0)
        except (TypeError, ValueError):
            reviews = 0

        if no_website and rating >= 4.5 and reviews >= 3:
            score += 15
            problems.append(f"Nota alta ({rating:.1f}) sem site (+15) — negócio bom, digital fraco")
        elif no_website and rating >= 4.0:
            score += 10
            problems.append(f"Boa reputação Maps ({rating:.1f}) (+10)")
        elif rating >= 3.5:
            score += 5
            problems.append(f"Reputação ok ({rating:.1f}) (+5)")

        if 3 <= reviews <= 40 and no_website:
            score += 5
            problems.append(f"Volume de avaliações saudável ({reviews}) (+5)")
        elif reviews > 100 and no_website:
            score += 2
            problems.append(f"Muitas avaliações ({reviews}) (+2)")

        # ── 4. Ticket / nicho (quem paga mais por site) ───────────────────
        niche = (company.get("niche") or "").strip().lower()
        if niche in self._NICHES_ALTO:
            score += 12
            problems.append(f"Nicho alto ticket ({niche}) (+12)")
            services.append("Site + agenda / captura de leads")
        elif niche in self._NICHES_MEDIO:
            score += 8
            problems.append(f"Nicho médio ({niche}) (+8)")
            services.append("Site institucional + WhatsApp")
        elif niche:
            score += 5
            problems.append(f"Nicho local ({niche}) (+5)")
        else:
            score += 3

        # ── 5. Instagram sem site (presença social solta) ─────────────────
        has_ig = (
            company.get("instagram_status") == "tem_instagram"
            or bool(company.get("instagram_url"))
            or bool(company.get("instagram_username"))
        )
        if no_website and has_ig:
            score += 8
            problems.append("Só Instagram / social (+8) — falta casa própria na web")
            services.append("Site + link na bio")
            if company.get("instagram_has_link") == 0:
                score += 3
                problems.append("Instagram sem link na bio (+3)")

        # ── 6. Cap 0–100 ─────────────────────────────────────────────────
        score = max(0, min(100, int(score)))

        # ── 7. Classificação ─────────────────────────────────────────────
        if no_website:
            lead_class = "raio"
        elif score >= 45:
            lead_class = "trovao"
        else:
            lead_class = "eco"

        if score >= 75:
            priority = "alta"   # quente
        elif score >= 50:
            priority = "media"  # morno
        else:
            priority = "baixa"  # frio

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
