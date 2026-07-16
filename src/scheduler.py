"""
scheduler.py — Agendador de Tarefas do Prospector Bot
=====================================================

Executa de forma periódica e automatizada os ciclos de busca de leads.
As buscas são disparadas nos horários configurados e rotacionam as cidades-alvo.

Cronograma Diário Padrão:
    - 08:00 (Restaurantes/Alimentação): restaurante, bar, pizzaria, hamburgueria, cafeteria
    - 10:00 (Estética): salão de beleza, barbearia, estética, manicure
    - 14:00 (Saúde): clínica, dentista, psicólogo, nutricionista, fisioterapia
    - 16:00 (Comércio): loja de roupa, pet shop, academia, oficina mecânica
    - 20:00 (Outros): escola, curso, imobiliária, escritório de advocacia
    - 22:00 (Relatório Diário): Consolida estatísticas para o painel de controle

Uso:
    from src.scheduler import start_scheduler
    start_scheduler()
"""

from __future__ import annotations

import json
import os
import psycopg2
import psycopg2.extras
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from loguru import logger

# Importações dos módulos do pipeline de prospecção
from src.google_maps import GoogleMapsSearcher
from src.website_checker import WebsiteChecker
from src.instagram_checker import InstagramChecker
from src.menu_checker import MenuChecker
from src.scorer import LeadScorer

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

load_dotenv()

_DATABASE_URL: str = os.getenv("DATABASE_URL", "")
_DB_PATH: str = os.getenv("DATABASE_PATH", "data/leads.db")

# Horários configuráveis do Scheduler (carregados via settings ou variáveis de ambiente)
TIME_REST: str = os.getenv("SCHEDULER_TIME_REST", "08:00")
TIME_BEAUTY: str = os.getenv("SCHEDULER_TIME_BEAUTY", "10:00")
TIME_HEALTH: str = os.getenv("SCHEDULER_TIME_HEALTH", "14:00")
TIME_TRADE: str = os.getenv("SCHEDULER_TIME_TRADE", "16:00")
TIME_OTHER: str = os.getenv("SCHEDULER_TIME_OTHER", "20:00")
TIME_REPORT: str = os.getenv("SCHEDULER_TIME_REPORT", "22:00")

# Mapeamento estático de grupos de nichos
_NICHE_GROUPS = {
    "restaurantes": ["restaurante", "bar", "pizzaria", "hamburgueria", "cafeteria"],
    "estetica":     ["salão de beleza", "barbearia", "estética", "manicure"],
    "saude":        ["clínica", "dentista", "psicólogo", "nutricionista", "fisioterapia"],
    "comercio":     ["loja de roupa", "pet shop", "academia", "oficina mecânica"],
    "outros":       ["escola", "curso", "imobiliária", "escritório de advocacia"]
}


# ---------------------------------------------------------------------------
# Estrutura do Banco de Dados
# ---------------------------------------------------------------------------

_SCHEDULER_TABLE_STATE = """
CREATE TABLE IF NOT EXISTS scheduler_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_SCHEDULER_TABLE_HISTORY = """
CREATE TABLE IF NOT EXISTS scheduler_history (
    id            SERIAL PRIMARY KEY,
    task_name     TEXT NOT NULL,
    niche_group   TEXT,
    city          TEXT,
    status        TEXT,               -- success | failed
    items_found   INTEGER DEFAULT 0,
    error_message TEXT,
    executed_at   TEXT NOT NULL,
    duration_s    REAL
);
"""

class SchedulerDatabase:
    """
    Controla o estado de pausa/retomada e logs históricos do agendador no PostgreSQL.
    """

    def __init__(self, db_path: str = _DB_PATH) -> None:
        # db_path mantido na assinatura para compatibilidade
        if not _DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL não configurada no .env. "
                "Defina a string de conexão PostgreSQL (ex: Supabase)."
            )
        self._init_db()

    def _connect(self):
        return psycopg2.connect(_DATABASE_URL)

    def _init_db(self) -> None:
        """Cria tabelas de histórico e estado do scheduler."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(_SCHEDULER_TABLE_STATE)
            cur.execute(_SCHEDULER_TABLE_HISTORY)
            # Insere valor padrão de pausa se não existir
            cur.execute(
                "INSERT INTO scheduler_state (key, value) VALUES ('paused', '0') ON CONFLICT DO NOTHING;"
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def is_paused(self) -> bool:
        """Verifica se o scheduler foi pausado pelo usuário via Dashboard."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT value FROM scheduler_state WHERE key = 'paused' LIMIT 1;"
            )
            row = cur.fetchone()
            cur.close()
            return row is not None and row[0] == "1"
        except Exception:
            return False
        finally:
            conn.close()

    def set_paused_state(self, paused: bool) -> None:
        """Define o estado de pausa do scheduler."""
        val = "1" if paused else "0"
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO scheduler_state (key, value) VALUES ('paused', %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
            """, (val,))
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def has_run_today(self, task_name: str) -> bool:
        """
        Evita duplicação: checa se a tarefa já foi executada com sucesso hoje.
        """
        today_str = datetime.now().strftime("%Y-%m-%d")
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM scheduler_history "
                "WHERE task_name = %s AND status = 'success' AND executed_at::date = %s::date LIMIT 1;",
                (task_name, today_str)
            )
            row = cur.fetchone()
            cur.close()
            return row is not None
        finally:
            conn.close()

    def log_run(
        self,
        task_name: str,
        niche_group: str,
        city: str,
        status: str,
        items_found: int = 0,
        error: str = "",
        duration: float = 0.0,
    ) -> None:
        """Adiciona o registro de execução para auditoria no Dashboard."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO scheduler_history "
                "(task_name, niche_group, city, status, items_found, error_message, executed_at, duration_s) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s);",
                (
                    task_name,
                    niche_group,
                    city,
                    status,
                    items_found,
                    error,
                    datetime.now().isoformat(),
                    duration,
                )
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Pipeline e Orquestração do Robô
# ---------------------------------------------------------------------------

_SOCIAL_URL_MARKERS = (
    "instagram.com", "facebook.com", "fb.com", "linktr.ee",
    "bio.link", "wa.me", "whatsapp.com", "tiktok.com",
)


def _has_real_website(website: str | None) -> bool:
    """True se o campo website aponta para site próprio (não só rede social)."""
    url = (website or "").strip()
    if not url:
        return False
    lower = url.lower()
    return not any(m in lower for m in _SOCIAL_URL_MARKERS)


class LeadGenerationPipeline:
    """
    Executa o fluxo unificado de busca e qualificação de leads.

    Foco comercial: só aprofunda análise em empresas SEM site próprio.
    Quem já tem site é marcado e ignorado (sem HTTP/Instagram).
    """

    def __init__(self, db_path: str = _DB_PATH) -> None:
        self.db_path = db_path
        self.maps = GoogleMapsSearcher(db_path)
        self.web = WebsiteChecker(db_path)
        self.insta = InstagramChecker(db_path)
        self.menu = MenuChecker(db_path)
        self.scorer = LeadScorer(db_path)

    def _skip_companies_with_website(self) -> int:
        """
        Marca em massa empresas que já têm site próprio.

        Evita gastar tempo em website_checker HTTP e Instagram.
        Retorna quantas foram descartadas nesta rodada.
        """
        if not _DATABASE_URL:
            return 0
        now = datetime.now().isoformat()
        sql = """
        UPDATE companies SET
            website_status = 'tem_site',
            website_checked_at = %s,
            instagram_checked_at = COALESCE(instagram_checked_at, %s),
            lead_class = 'eco',
            lead_score = 0,
            lead_priority = 'baixa',
            lead_problems = '[]',
            lead_services = '[]',
            scored_at = %s
        WHERE website_checked_at IS NULL
          AND website IS NOT NULL
          AND TRIM(website) <> ''
          AND website NOT ILIKE '%%instagram.com%%'
          AND website NOT ILIKE '%%facebook.com%%'
          AND website NOT ILIKE '%%fb.com%%'
          AND website NOT ILIKE '%%linktr.ee%%'
          AND website NOT ILIKE '%%bio.link%%'
          AND website NOT ILIKE '%%tiktok.com%%'
          AND website NOT ILIKE '%%whatsapp.com%%'
          AND website NOT ILIKE '%%wa.me%%'
        """
        try:
            conn = psycopg2.connect(_DATABASE_URL)
            cur = conn.cursor()
            cur.execute(sql, (now, now, now))
            skipped = cur.rowcount
            conn.commit()
            cur.close()
            conn.close()
            if skipped:
                logger.info(
                    f"[Pipeline] {skipped} empresas COM site próprio ignoradas "
                    f"(sem análise extra)."
                )
            return skipped
        except Exception as exc:
            logger.warning(f"[Pipeline] Falha ao pular empresas com site: {exc}")
            return 0

    def execute_flow(
        self,
        niche: str,
        city: str,
        state: str,
        query_term: str | None = None,
        max_results: int = 25,
    ) -> int:
        """
        Roda o ciclo do pipeline completo:
            1. google_maps.py      → Busca locais
            2. Skip em massa quem já tem site
            3. website_checker.py  → só pendentes sem site
            4. instagram_checker.py → só sem site
            5. scorer.py           → score (Raio = sem site)

        Args:
            niche:       ID do nicho salvo no banco (ex: "odontologia")
            city:        Cidade (ex: "Curitiba")
            state:       Estado (ex: "PR")
            query_term:  Texto de busca no Maps (opcional)
            max_results: Meta de empresas NOVAS sem site (já no banco não contam)

        Returns:
            Quantidade de leads novos sem site salvos nesta rodada.
        """
        search_query = (query_term or niche).strip()
        logger.info(
            f"[Pipeline] Iniciando fluxo: {niche} ({search_query}) em {city} - {state} "
            f"| meta {max_results} NOVAS sem site"
        )

        # 1. Busca Google Maps (cota = só novas sem site)
        companies = self.maps.search(
            niche=niche,
            city=city,
            state=state,
            max_results=max_results,
            query_term=search_query,
        )
        total_found = len(companies)

        if total_found == 0:
            logger.info(
                f"[Pipeline] Nenhuma empresa NOVA sem site para {niche} em {city} "
                f"(as do topo do Maps já estavam no banco ou tinham site)."
            )
            return 0

        no_site = total_found  # search() já filtra: só novas sem site
        logger.info(
            f"[Pipeline] {city}/{niche}: {no_site} leads NOVOS sem site para analisar"
        )

        # 2. Descarta quem já tem site — zero tempo extra
        self._skip_companies_with_website()

        # 3. Sites: só pendentes (sem site / social)
        logger.info("[Pipeline] Marcando empresas sem site...")
        self.web.check_all(limit=max(no_site * 2, 30), delay_between_s=0.3)

        # 4. Instagram: só leads sem site (oportunidade Raio)
        logger.info("[Pipeline] Instagram apenas em leads sem site...")
        self.insta.check_all(limit=max(no_site * 2, 30), delay_between_s=0.8)

        # 5. Re-score (score já foi dado na hora do save; aqui atualiza com Instagram)
        logger.info("[Pipeline] Re-score com dados de Instagram (se houver)...")
        for c in companies:
            cid = c.get("id")
            if cid:
                try:
                    self.scorer.score_one(int(cid))
                except Exception as exc:
                    logger.debug(f"[Pipeline] Re-score id={cid}: {exc}")
        # Pendentes antigos sem score (fallback)
        self.scorer.score_all()

        logger.info(f"[Pipeline] Fluxo completo para {niche} em {city} finalizado.")
        return total_found


# ---------------------------------------------------------------------------
# Orquestrador do Agendador
# ---------------------------------------------------------------------------

class BotScheduler:
    """
    Gerencia e dispara o APScheduler em background.
    """

    def __init__(self, db_path: str = _DB_PATH) -> None:
        self.db = SchedulerDatabase(db_path)
        self.pipeline = LeadGenerationPipeline(db_path)
        self.scheduler = BackgroundScheduler()

    def _get_active_city(self) -> dict[str, Any] | None:
        """
        Rotaciona e seleciona a cidade ativa baseado no dia do ano.
        
        Lê as cidades do config/cities.json e retorna a correspondente ao dia.
        """
        cities_path = Path("config/cities.json")
        if not cities_path.exists():
            return None
            
        try:
            with open(cities_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                active_cities = [c for c in data.get("cidades", []) if c.get("ativo") is True]
                
                if not active_cities:
                    return None
                    
                # Rotaciona baseado no dia do ano
                day_of_year = datetime.now().timetuple().tm_yday
                city_index = day_of_year % len(active_cities)
                return active_cities[city_index]
        except Exception as exc:
            logger.error(f"[Scheduler] Falha ao rotacionar cidades: {exc}")
            return None

    def _execute_niche_search(self, task_name: str, niche_group_key: str) -> None:
        """
        Método wrapper de execução chamado pelo APScheduler.
        
        Verifica o estado de pausa e histórico antes de disparar o pipeline.
        """
        if self.db.is_paused():
            logger.info(f"[Scheduler] Execucao da tarefa '{task_name}' suspensa (Scheduler PAUSADO).")
            return
            
        if self.db.has_run_today(task_name):
            logger.info(f"[Scheduler] Tarefa '{task_name}' ja foi executada com sucesso hoje. Ignorando.")
            return

        city_data = self._get_active_city()
        if not city_data:
            logger.warning("[Scheduler] Nenhuma cidade ativa configurada no cities.json.")
            return

        city_name = city_data["nome"]
        state = city_data["estado"]
        niches = _NICHE_GROUPS.get(niche_group_key, [])

        logger.info(
            f"[Scheduler] Iniciando Job '{task_name}' para {city_name} - {state}. "
            f"Nichos: {niches}"
        )

        t0 = time.monotonic()
        items_processed = 0
        status = "success"
        error_msg = ""

        try:
            # Executa o pipeline para cada nicho do grupo selecionado
            for niche in niches:
                items_processed += self.pipeline.execute_flow(niche, city_name, state)
        except Exception as exc:
            status = "failed"
            error_msg = str(exc)
            logger.error(f"[Scheduler] Falha na execucao do Job '{task_name}': {exc}")
            
            # Re-agenda tentativa em 30 minutos em caso de falha pontual
            logger.info(f"[Scheduler] Re-agendando tarefa '{task_name}' para tentativa em 30 min.")
            self.scheduler.add_job(
                func=self._execute_niche_search,
                trigger="date",
                run_date=datetime.now() + timedelta(minutes=30),
                args=[task_name, niche_group_key],
                id=f"{task_name}_retry"
            )

        duration = time.monotonic() - t0
        self.db.log_run(
            task_name=task_name,
            niche_group=niche_group_key,
            city=city_name,
            status=status,
            items_found=items_processed,
            error=error_msg,
            duration=round(duration, 2)
        )
        logger.info(f"[Scheduler] Job '{task_name}' finalizado. Status={status} Duracao={duration:.1f}s")

    def _execute_daily_report(self) -> None:
        """Tarefa de fechamento do dia: gera e salva estatísticas de relatórios."""
        if self.db.is_paused():
            return
            
        logger.info("[Scheduler] Executando consolidacao de relatorio diario...")
        t0 = time.monotonic()
        
        try:
            # Roda scorer geral para consolidar qualquer dado restante
            scorer = LeadScorer(self.db_path)
            leads = scorer.score_all()
            
            logger.info(f"[Scheduler] Relatorio consolidado. Total de leads qualificados no sistema: {len(leads)}")
            self.db.log_run("relatorio_diario", "relatorios", "todas", "success", len(leads), duration=time.monotonic() - t0)
        except Exception as exc:
            logger.error(f"[Scheduler] Erro ao consolidar relatorio diario: {exc}")
            self.db.log_run("relatorio_diario", "relatorios", "todas", "failed", error=str(exc), duration=time.monotonic() - t0)

    # ------------------------------------------------------------------
    # Gerenciamento de Ciclo de Vida do Scheduler
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Inicia e agenda as tarefas diárias no APScheduler."""
        logger.info("[Scheduler] Inicializando APScheduler...")

        # Agenda 08:00 - Alimentação
        h_rest, m_rest = TIME_REST.split(":")
        self.scheduler.add_job(
            func=self._execute_niche_search,
            trigger="cron",
            hour=h_rest,
            minute=m_rest,
            args=["busca_alimentacao", "restaurantes"],
            id="job_alimentacao"
        )

        # Agenda 10:00 - Estética
        h_beauty, m_beauty = TIME_BEAUTY.split(":")
        self.scheduler.add_job(
            func=self._execute_niche_search,
            trigger="cron",
            hour=h_beauty,
            minute=m_beauty,
            args=["busca_estetica", "estetica"],
            id="job_estetica"
        )

        # Agenda 14:00 - Saúde
        h_health, m_health = TIME_HEALTH.split(":")
        self.scheduler.add_job(
            func=self._execute_niche_search,
            trigger="cron",
            hour=h_health,
            minute=m_health,
            args=["busca_saude", "saude"],
            id="job_saude"
        )

        # Agenda 16:00 - Comércio
        h_trade, m_trade = TIME_TRADE.split(":")
        self.scheduler.add_job(
            func=self._execute_niche_search,
            trigger="cron",
            hour=h_trade,
            minute=m_trade,
            args=["busca_comercio", "comercio"],
            id="job_comercio"
        )

        # Agenda 20:00 - Outros
        h_other, m_other = TIME_OTHER.split(":")
        self.scheduler.add_job(
            func=self._execute_niche_search,
            trigger="cron",
            hour=h_other,
            minute=m_other,
            args=["busca_outros", "outros"],
            id="job_outros"
        )

        # Agenda 22:00 - Relatório
        h_report, m_report = TIME_REPORT.split(":")
        self.scheduler.add_job(
            func=self._execute_daily_report,
            trigger="cron",
            hour=h_report,
            minute=m_report,
            id="job_relatorio"
        )

        self.scheduler.start()
        logger.info(
            f"[Scheduler] Agendamento ativo. Horários: Alimentação={TIME_REST} | "
            f"Estética={TIME_BEAUTY} | Saúde={TIME_HEALTH} | Comércio={TIME_TRADE} | "
            f"Outros={TIME_OTHER} | Relatório={TIME_REPORT}"
        )

    def stop(self) -> None:
        """Desliga o scheduler de forma segura."""
        logger.info("[Scheduler] Encerrando agendador de tarefas...")
        self.scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Funções Globais de Conveniência
# ---------------------------------------------------------------------------

_scheduler_instance: BotScheduler | None = None

def start_scheduler() -> None:
    """Inicia a instância global do scheduler em background."""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = BotScheduler()
        _scheduler_instance.start()

def stop_scheduler() -> None:
    """Desliga a instância global do scheduler."""
    global _scheduler_instance
    if _scheduler_instance is not None:
        _scheduler_instance.stop()
        _scheduler_instance = None


# ---------------------------------------------------------------------------
# Execução Standalone (Thread/Processo separado)
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
        start_scheduler()
        
        # Mantém o processo vivo
        print("\n[Scheduler] Rodando em background. Pressione CTRL+C para sair.\n")
        while True:
            time.sleep(1)
            
    except (KeyboardInterrupt, SystemExit):
        stop_scheduler()
        print("\n[Scheduler] Agendador parado com sucesso.\n")
    except FileNotFoundError as e:
        print(f"\n❌ Erro: {e}")
        print("   Configure e popule o banco SQLite antes de rodar o agendador.\n")
