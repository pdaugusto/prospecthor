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

# Só pontua quem ainda não foi pontuado (empresas com site já saem marcadas no pipeline)
_SELECT_ALL_COLLECTED_SQL = """
SELECT * FROM companies
WHERE (business_status IS NULL OR business_status != 'CLOSED_PERMANENTLY')
  AND website_checked_at IS NOT NULL
  AND scored_at IS NULL
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

    def calculate_score(self, company: dict[str, Any]) -> dict[str, Any]:
        """
        Aplica as regras da tabela de pontuação sobre os dados da empresa.

        Regra de negócio (foco comercial):
            ⚡ RAIO  = SEM site próprio  → único tipo listado no dashboard
            ☁️ TROVÃO / 🔊 ECO = tem site (com ou sem problemas) → não listados

        Args:
            company: Registro da tabela de empresas com dados enriquecidos.

        Returns:
            Dicionário com o score calculado, classificação, problemas e serviços sugeridos.
        """
        score = 0
        problems = []
        services = []
        no_website = self._has_no_website(company)

        # ── 1. Critérios de Site ──────────────────────────────────────────
        web_flags = (company.get("website_flags") or "").split(",")

        if no_website:
            # Lead Raio: sem site é o critério principal de oportunidade
            score += 55
            problems.append("Sem site (+55)")
            services.append("Site profissional")
        else:
            # Tem site: pontua problemas de qualidade (nunca vira Raio)
            if "sem_https" in web_flags or company.get("website_https") == 0:
                score += 15
                problems.append("Site sem HTTPS (+15)")
                services.append("Certificado SSL")

            speed = company.get("website_speed_s")
            if speed is not None and speed > 5.0:
                score += 15
                problems.append(f"Site lento ({speed:.1f}s) (+15)")
                services.append("Otimização de velocidade")

            if "nao_mobile" in web_flags or company.get("website_mobile") == 0:
                score += 15
                problems.append("Site não mobile-friendly (+15)")
                services.append("Design responsivo")

        # ── 2. Critérios de Instagram ─────────────────────────────────────
        has_ig = (
            company.get("instagram_status") == "tem_instagram"
            or bool(company.get("instagram_url"))
            or bool(company.get("instagram_username"))
        )
        if has_ig and company.get("instagram_has_link") == 0:
            score += 15
            problems.append("Tem Instagram mas sem link na bio (+15)")
            services.append("Conectar Instagram ao site")

        # ── 3. Classificação ──────────────────────────────────────────────
        # Raio = exclusivamente empresas sem site próprio
        if no_website:
            lead_class = "raio"
            priority = "alta"
        elif score >= 30:
            lead_class = "trovao"
            priority = "media"
        else:
            lead_class = "eco"
            priority = "baixa"

        dedup_services = []
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

    def score_all(self) -> list[dict[str, Any]]:
        """
        Carrega as empresas recolhidas, calcula o lead score definitivo,
        salva no banco SQLite e retorna os dados ordenados por score decrescente.
        """
        companies = self.db.get_collected_companies()
        if not companies:
            logger.info("[LeadScorer] Nenhuma empresa com dados analisados encontrada no banco.")
            return []

        logger.info(f"[LeadScorer] Iniciando qualificação de {len(companies)} empresas...")

        scored_leads = []
        raio_count = 0
        for company in companies:
            result = self.calculate_score(company)
            self.db.save_lead_score(result)

            # Mescla dados originais com resultado para exibição e ordenação
            company.update(result)
            scored_leads.append(company)
            if result["lead_class"] == "raio":
                raio_count += 1

        # Ordena leads por score decrescente (maior pontuação = prioridade máxima)
        scored_leads.sort(key=lambda x: x["lead_score"], reverse=True)

        logger.info(
            f"[LeadScorer] {len(scored_leads)} empresas pontuadas | "
            f"{raio_count} ⚡ Raio (sem site) listáveis no dashboard."
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
            print(f"│ {title_tag:<22} — Score: {score:<4}/150              │")
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
