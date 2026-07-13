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
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from loguru import logger

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

load_dotenv()

_DB_PATH: str = os.getenv("DATABASE_PATH", "data/leads.db")

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
    lead_score    = :lead_score,
    lead_class    = :lead_class,
    lead_problems = :lead_problems,
    lead_services = :lead_services,
    lead_priority = :lead_priority,
    scored_at     = :scored_at
WHERE id = :id;
"""

_SELECT_ALL_COLLECTED_SQL = """
SELECT * FROM companies
WHERE (business_status IS NULL OR business_status != 'CLOSED_PERMANENTLY')
  AND (website_checked_at IS NOT NULL OR instagram_checked_at IS NOT NULL)
ORDER BY id;
"""


# ---------------------------------------------------------------------------
# Banco de dados do Scorer
# ---------------------------------------------------------------------------

class ScorerDatabase:
    """
    Gerencia transações no SQLite específicas para o qualificador de leads.
    """

    def __init__(self, db_path: str = _DB_PATH) -> None:
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"Banco de dados não encontrado: {self.db_path}\n"
                "Execute os checkers para popular e enriquecer os leads antes do scorer."
            )
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _migrate(self) -> None:
        """Adiciona colunas de qualificação à tabela de empresas se necessário."""
        with self._connect() as conn:
            existing = {
                row[1]
                for row in conn.execute("PRAGMA table_info(companies);").fetchall()
            }
            added = []
            for col_name, col_type in _SCORER_COLUMNS:
                if col_name not in existing:
                    try:
                        conn.execute(
                            f"ALTER TABLE companies ADD COLUMN {col_name} {col_type};"
                        )
                        added.append(col_name)
                    except sqlite3.OperationalError as exc:
                        logger.warning(f"[DB] Coluna {col_name!r} não adicionada: {exc}")
            if added:
                conn.commit()
                logger.info(f"[DB] Migração Scorer: {len(added)} colunas adicionadas → {added}")
            else:
                logger.debug("[DB] Schema Scorer já atualizado.")

    def get_collected_companies(self) -> list[dict[str, Any]]:
        """Busca todas as empresas que já passaram por análise de site ou instagram."""
        with self._connect() as conn:
            rows = conn.execute(_SELECT_ALL_COLLECTED_SQL).fetchall()
            return [dict(row) for row in rows]

    def save_lead_score(self, result: dict[str, Any]) -> None:
        """Persiste os resultados da pontuação de lead."""
        try:
            with self._connect() as conn:
                conn.execute(_SAVE_SCORER_SQL, result)
                conn.commit()
        except sqlite3.Error as exc:
            logger.error(f"[DB] Erro ao salvar pontuação de lead id={result.get('id')}: {exc}")

    def get_stats(self) -> dict[str, Any]:
        """Retorna estatísticas dos leads classificados."""
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM companies WHERE scored_at IS NOT NULL"
            ).fetchone()[0]
            by_class = conn.execute(
                "SELECT lead_class, COUNT(*) as cnt FROM companies "
                "WHERE scored_at IS NOT NULL "
                "GROUP BY lead_class ORDER BY cnt DESC;"
            ).fetchall()
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

    def calculate_score(self, company: dict[str, Any]) -> dict[str, Any]:
        """
        Aplica as regras da tabela de pontuação sobre os dados da empresa.

        Args:
            company: Registro da tabela de empresas com dados enriquecidos.

        Returns:
            Dicionário com o score calculado, classificação, problemas e serviços sugeridos.
        """
        score = 0
        problems = []
        services = []

        # ── 1. Critérios de Site ──────────────────────────────────────────
        web_status = company.get("website_status")
        web_flags = (company.get("website_flags") or "").split(",")

        # Se NÃO tem site:
        if not company.get("website") or web_status == "sem_site":
            score += 40
            problems.append("Sem site (+40)")
            services.append("Site profissional")
        else:
            # Se TEM site, avalia os problemas do site:
            # 1. SSL/HTTPS (+15)
            if "sem_https" in web_flags or company.get("website_https") == 0:
                score += 15
                problems.append("Site sem HTTPS (+15)")
                services.append("Certificado SSL")
            
            # 2. Velocidade / Lentidão (>5s) (+15)
            speed = company.get("website_speed_s")
            if speed is not None and speed > 5.0:
                score += 15
                problems.append(f"Site lento ({speed:.1f}s) (+15)")
                services.append("Otimização de velocidade")
            
            # 3. Responsividade / Mobile-friendly (+15)
            if "nao_mobile" in web_flags or company.get("website_mobile") == 0:
                score += 15
                problems.append("Site não mobile-friendly (+15)")
                services.append("Design responsivo")

        # ── 2. Critérios de Instagram ─────────────────────────────────────
        # Tem Instagram mas sem link na bio (+15)
        # Verifica se a empresa tem uma URL de Instagram ou username
        has_ig = company.get("instagram_status") == "tem_instagram" or bool(company.get("instagram_url")) or bool(company.get("instagram_username"))
        if has_ig and company.get("instagram_has_link") == 0:
            score += 15
            problems.append("Tem Instagram mas sem link na bio (+15)")
            services.append("Conectar Instagram ao site")

        # ── 3. Classificação do Lead (Escala de 100 pontos) ────────────────
        if score >= 55:
            lead_class = "raio"
            priority = "alta"
        elif score >= 30:
            lead_class = "trovao"
            priority = "media"
        else:
            lead_class = "eco"
            priority = "baixa"

        # Dedup de serviços sugeridos
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
        for company in companies:
            result = self.calculate_score(company)
            self.db.save_lead_score(result)
            
            # Mescla dados originais com resultado para exibição e ordenação
            company.update(result)
            scored_leads.append(company)

        # Ordena leads por score decrescente (maior pontuação = prioridade máxima)
        scored_leads.sort(key=lambda x: x["lead_score"], reverse=True)

        logger.info(f"[LeadScorer] {len(scored_leads)} leads qualificados e ordenados com sucesso.")
        return scored_leads

    def print_resumos(self, limit: int = 10) -> None:
        """
        Gera e exibe um resumo textual formatado em box ASCII dos principais leads quentes/mornos.
        """
        leads = self.score_all()
        # Filtra apenas leads raio ou trovão
        qualificados = [l for l in leads if l["lead_class"] in ("raio", "trovao")][:limit]

        if not qualificados:
            print("\nNenhum lead raio ou trovão qualificado para exibição no momento.\n")
            return

        print(f"\n{'═' * 60}")
        print(f"      RESUMO DOS MELHORES LEADS ENCONTRADOS ({len(qualificados)} principais)")
        print(f"{'═' * 60}\n")

        for lead in qualificados:
            title_tag = "⚡ LEAD RAIO" if lead["lead_class"] == "raio" else "☁️ LEAD TROVÃO"
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
