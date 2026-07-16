"""
main.py — Interface de Linha de Comando (CLI) do Prospector Bot
==============================================================

Ponto de entrada unificado para rodar buscas sob demanda, gerenciar o
agendamento diário, iniciar o painel administrativo ou exportar leads.

Comandos disponíveis:
    python main.py run          → Roda a busca completa agora
    python main.py schedule     → Inicia o agendamento em background
    python main.py dashboard    → Abre o painel web administrativo na porta 5000
    python main.py all          → Inicia scheduler + dashboard no mesmo processo
    python main.py export       → Exporta dados para planilhas CSV
    python main.py status       → Exibe estatísticas de performance do bot
"""

from __future__ import annotations

import os
import sys
import time
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import click
from loguru import logger
from dotenv import load_dotenv

# Configura o logger do Loguru antes das importações para garantir o formato correto
logger.remove()
# Formato personalizado: [DATA] [MÓDULO] [NÍVEL] Mensagem
_LOG_FORMAT = "[{time:YYYY-MM-DD HH:mm:ss}] [{name}] [{level}] {message}"

# Handler para console (stdout) com cores
logger.add(
    sink=sys.stdout,
    format="<green>[{time:YYYY-MM-DD HH:mm:ss}]</green> <cyan>[{name}]</cyan> <level>[{level}]</level> {message}",
    colorize=True,
    level="INFO"
)

# Handler para arquivo bot.log (sem cores)
logger.add(
    sink="bot.log",
    format="[{time:YYYY-MM-DD HH:mm:ss}] [{name}] [{level}] {message}",
    colorize=False,
    rotation="10 MB",
    retention="30 days",
    level="DEBUG"
)

# Importações locais do projeto
from config.settings import settings
from src.scheduler import BotScheduler, start_scheduler, stop_scheduler
from src.exporter import LeadExporter
from src.google_maps import Database


# ---------------------------------------------------------------------------
# Setup do banco de dados
# ---------------------------------------------------------------------------

def _get_db_connection() -> sqlite3.Connection:
    load_dotenv()
    db_path = os.getenv("DATABASE_PATH", "data/leads.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Grupos e Comandos Click CLI
# ---------------------------------------------------------------------------

@click.group()
def cli() -> None:
    """🔎 PROSPECTOR BOT — Automação de Prospecção Comercial Local."""
    pass


# ── COMANDO: run ───────────────────────────────────────────────────────────

@cli.command("run")
@click.option("--niche", "-n", default=None, help="Nicho de negócio específico (ex: restaurante).")
@click.option("--cidade", "-c", default=None, help="Cidade para a busca (ex: 'Porto Alegre').")
@click.option("--estado", "-e", default=None, help="Estado da cidade (ex: RS).")
def run_now(niche: str | None, cidade: str | None, estado: str | None) -> None:
    """Executa o pipeline completo de prospecção agora.

    Checkpoint é por EMPRESA (place_id no Supabase), não por cidade:
    empresas já salvas não reabrem o painel no Maps; quem tem site não grava.
    """
    logger.info("Bot iniciado com sucesso.")

    from src.checkpoint import CompanyCheckpoint
    known = CompanyCheckpoint.load()
    logger.info(
        f"Checkpoint por empresa: {len(known)} place_ids já processados no banco."
    )

    # Se não forem informados nicho/cidade específicos, roda para os ativos dos arquivos config
    if not niche or not cidade:
        logger.info("Nenhum nicho/cidade especificado. Lendo do niches.json e cities.json...")

        niches_path = Path("config/niches.json")
        cities_path = Path("config/cities.json")

        if not niches_path.exists() or not cities_path.exists():
            logger.error("Arquivos de configuração config/niches.json ou config/cities.json ausentes.")
            sys.exit(1)

        try:
            with open(niches_path, "r", encoding="utf-8") as f:
                niches_data = json.load(f).get("nichos", [])
            with open(cities_path, "r", encoding="utf-8") as f:
                cities_data = json.load(f).get("cidades", [])
        except Exception as exc:
            logger.error(f"Falha ao ler arquivos JSON: {exc}")
            sys.exit(1)

        active_cities = [c for c in cities_data if c.get("ativo") is True]
        if not active_cities or not niches_data:
            logger.warning("Nenhuma cidade ou nicho ativo para processamento automático.")
            return

        # Prioridade: alta → media → baixa; evita ficar em praça já saturada
        prio = {"alta": 0, "media": 1, "baixa": 2, "pausada": 9}
        active_cities.sort(key=lambda c: (prio.get(c.get("priority", "media"), 5), c.get("nome", "")))

        from src.scheduler import LeadGenerationPipeline
        pipeline = LeadGenerationPipeline()

        # Contagem no banco: pula cidade+nicho já bem varridos (ex: SP/RJ antigos)
        coverage: dict[tuple[str, str], int] = {}
        try:
            import os
            import psycopg2
            db_url = os.getenv("DATABASE_URL", "")
            if db_url:
                conn = psycopg2.connect(db_url)
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT lower(city), lower(niche), COUNT(*)
                    FROM companies
                    GROUP BY lower(city), lower(niche)
                    """
                )
                for city_l, niche_l, n in cur.fetchall():
                    coverage[(city_l or "", niche_l or "")] = int(n)
                cur.close()
                conn.close()
        except Exception as exc:
            logger.warning(f"Não foi possível ler cobertura do banco: {exc}")

        MAX_PER_CITY_NICHE = 40  # se já tem 40+ no banco, vai pra outra praça

        total_pairs = len(active_cities) * len(niches_data)
        logger.info(
            f"Lote Brasil: {len(active_cities)} cidades ativas × {len(niches_data)} nichos "
            f"| SP/RJ/Goiânia pausados no cities.json | bairros nas queries | "
            f"skip se ≥{MAX_PER_CITY_NICHE} no banco."
        )

        ran = 0
        skipped_sat = 0
        idx = 0
        for city in active_cities:
            c_name = city["nome"]
            c_state = city["estado"]
            bairros = city.get("bairros") or []
            for n in niches_data:
                n_id = n["id"]
                q_term = n.get("query_term") or n_id
                idx += 1

                key = (c_name.lower(), n_id.lower())
                # também tenta sem acento simples (Goiania vs Goiânia)
                already = coverage.get(key, 0)
                if already >= MAX_PER_CITY_NICHE:
                    skipped_sat += 1
                    logger.info(
                        f"[{idx}/{total_pairs}] ⏭ Saturado: {n_id} em {c_name}-{c_state} "
                        f"({already} no banco ≥ {MAX_PER_CITY_NICHE}) — próxima praça"
                    )
                    continue

                try:
                    logger.info(
                        f"[{idx}/{total_pairs}] ▶ {n_id} em {c_name} - {c_state} "
                        f"| bairros={len(bairros)} | já no DB={already} | query: {q_term}"
                    )
                    pipeline.execute_flow(
                        niche=n_id,
                        city=c_name,
                        state=c_state,
                        query_term=q_term,
                        max_results=25,
                        bairros=bairros,
                    )
                    ran += 1
                except Exception as exc:
                    logger.error(
                        f"Falha ao rodar pipeline para {n_id} em {c_name}: {exc}"
                    )

        logger.info(
            f"Lote Brasil finalizado: {ran} executados | "
            f"{skipped_sat} pulados (praça saturada)."
        )
    else:
        state_val = estado or "RS"
        from src.scheduler import LeadGenerationPipeline
        pipeline = LeadGenerationPipeline()
        try:
            query_term = niche
            niches_path = Path("config/niches.json")
            if niches_path.exists():
                try:
                    with open(niches_path, "r", encoding="utf-8") as f:
                        for n in json.load(f).get("nichos", []):
                            if n.get("id") == niche:
                                query_term = n.get("query_term") or niche
                                break
                except Exception:
                    pass

            # Bairros opcionais se a cidade estiver no cities.json
            bairros: list = []
            cities_path = Path("config/cities.json")
            if cities_path.exists():
                try:
                    with open(cities_path, "r", encoding="utf-8") as f:
                        for c in json.load(f).get("cidades", []):
                            if (c.get("nome") or "").lower() == (cidade or "").lower():
                                bairros = c.get("bairros") or []
                                break
                except Exception:
                    pass

            pipeline.execute_flow(
                niche=niche,
                city=cidade,
                state=state_val,
                query_term=query_term,
                max_results=25,
                bairros=bairros,
            )
        except Exception as exc:
            logger.error(f"Erro ao processar busca manual: {exc}")
            sys.exit(1)


# ── COMANDO: schedule ──────────────────────────────────────────────────────

@cli.command("schedule")
def run_schedule() -> None:
    """Inicia o Scheduler em background e monitora as execuções."""
    logger.info("Bot iniciado com sucesso.")
    logger.info("Iniciando scheduler de tarefas recorrentes em background...")
    
    try:
        start_scheduler()
        click.echo(click.style("\n[Scheduler] Executando. Pressione CTRL+C para parar.\n", fg="green", bold=True))
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        stop_scheduler()
        logger.info("Scheduler encerrado pelo operador.")
        click.echo(click.style("\n[Scheduler] Parado com sucesso.\n", fg="yellow"))


# ── COMANDO: dashboard ─────────────────────────────────────────────────────

@cli.command("dashboard")
def run_dashboard() -> None:
    """Inicia o painel de controle administrativo Flask na porta 5000."""
    logger.info("Dashboard iniciado com sucesso.")
    logger.info("Disparando Flask local na porta 5000...")
    
    # Executa import inline para não iniciar recursos se o comando for outro
    from dashboard import app
    app.run(host="0.0.0.0", port=5000, debug=False)


# ── COMANDO: all (Scheduler + Dashboard) ───────────────────────────────────

@cli.command("all")
def run_all() -> None:
    """Modo unificado: Inicia o Scheduler (background) e o Dashboard (web)."""
    logger.info("Bot iniciado com sucesso no modo unificado.")
    logger.info("Iniciando APScheduler em background...")
    
    try:
        # Inicia scheduler em thread em background
        start_scheduler()
        
        logger.info("Iniciando Dashboard Web na porta 5000...")
        # Bloqueia a thread principal rodando o Flask
        from dashboard import app
        app.run(host="0.0.0.0", port=5000, debug=False)
        
    except (KeyboardInterrupt, SystemExit):
        stop_scheduler()
        logger.info("Serviço unificado interrompido com sucesso.")


# ── COMANDO: export ────────────────────────────────────────────────────────

@cli.command("export")
@click.option("--tipo", "-t", default="todos", type=click.Choice(["todos", "raio"]), help="Exportar todos os leads ou apenas leads do tipo Raio.")
@click.option("--cidade", "-c", default=None, help="Filtrar por cidade (ex: porto_alegre).")
@click.option("--nicho", "-n", default=None, help="Filtrar por nicho (ex: restaurante).")
def run_export(tipo: str, cidade: str | None, nicho: str | None) -> None:
    """Exporta os leads qualificados do SQLite para planilhas CSV."""
    exporter = LeadExporter()
    
    try:
        if cidade:
            path = exporter.exportar_por_cidade(cidade)
            click.echo(click.style(f"✓ Leads da cidade '{cidade}' salvos em: {path}", fg="green"))
        elif nicho:
            path = exporter.exportar_por_nicho(nicho)
            click.echo(click.style(f"✓ Leads do nicho '{nicho}' salvos em: {path}", fg="green"))
        elif tipo == "raio":
            path = exporter.exportar_quentes()
            click.echo(click.style(f"✓ Leads Raio salvos em: {path}", fg="green"))
        else:
            path = exporter.exportar_todos()
            click.echo(click.style(f"✓ Todos os leads salvos em: {path}", fg="green"))
    except Exception as exc:
        logger.error(f"Erro ao exportar dados: {exc}")
        sys.exit(1)


# ── COMANDO: status ────────────────────────────────────────────────────────

@cli.command("status")
def run_status() -> None:
    """Exibe estatísticas consolidadas e status de execução do bot."""
    click.echo(click.style("\n🔎 STATUS DO PROSPECTOR BOT", fg="cyan", bold=True))
    click.echo(click.style("=" * 45, fg="cyan"))

    # 1. Conexão e contadores
    try:
        conn = _get_db_connection()
        total_leads = conn.execute("SELECT COUNT(*) FROM companies WHERE lead_score IS NOT NULL").fetchone()[0]
        
        quentes = conn.execute("SELECT COUNT(*) FROM companies WHERE lead_class = 'raio'").fetchone()[0]
        mornos = conn.execute("SELECT COUNT(*) FROM companies WHERE lead_class = 'trovao'").fetchone()[0]
        frios = conn.execute("SELECT COUNT(*) FROM companies WHERE lead_class = 'eco'").fetchone()[0]
        descartas = 0
        
        # Última execução
        ultimo_run = conn.execute(
            "SELECT task_name, city, status, executed_at FROM scheduler_history "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        
        conn.close()
    except Exception:
        # Fallback se banco não existir
        total_leads = 0
        quentes, mornos, frios, descartas = 0, 0, 0, 0
        ultimo_run = None

    # Exibe informações estruturadas
    click.echo(f"  Leads qualificados no banco : {total_leads}")
    click.echo(f"   ⚡ Leads Raio              : {quentes}")
    click.echo(f"   ☁️ Leads Trovão            : {mornos}")
    click.echo(f"   🍃 Leads Eco               : {frios}")
    click.echo(click.style("-" * 45, fg="cyan"))
    
    if ultimo_run:
        data_exec = ultimo_run["executed_at"].replace("T", " ")[:16] if ultimo_run["executed_at"] else "--"
        status_style = "green" if ultimo_run["status"] == "success" else "red"
        click.echo(f"  Última busca registrada     : {ultimo_run['task_name']}")
        click.echo(f"   📍 Cidade                  : {ultimo_run['city']}")
        click.echo(f"   📅 Executado em            : {data_exec}")
        click.echo(f"   ⚡ Status                  : " + click.style(ultimo_run['status'].upper(), font_weight=True if hasattr(click, 'style') else None, fg=status_style, bold=True))
    else:
        click.echo("  Última busca registrada     : Nenhuma execução encontrada.")
        
    click.echo(click.style("=" * 45 + "\n", fg="cyan"))


# ---------------------------------------------------------------------------
# Inicialização Global
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
