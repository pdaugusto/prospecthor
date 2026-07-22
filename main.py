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
@click.option(
    "--limit-jobs",
    default=0,
    type=int,
    help="Máximo de jobs (bairro×nicho) nesta sessão. 0 = todos pendentes.",
)
def run_now(
    niche: str | None,
    cidade: str | None,
    estado: str | None,
    limit_jobs: int,
) -> None:
    """Lote inteligente: 1 job = 1 bairro + 1 nicho.

    - Não repete área já varrida (data/search_coverage.json)
    - SP/RJ só em bairros NOVOS (ja_varridos no cities.json)
    - Só grava SEM site + score na hora
    - Instagram off por padrão (SKIP_INSTAGRAM=true) → mais leads/dia
    """
    logger.info("Bot iniciado com sucesso.")

    import atexit
    from src.checkpoint import CompanyCheckpoint
    from src.coverage import list_pending_jobs, mark_done, is_done
    from src.bot_status import set_status, add_log, increment_session_leads

    # Se o processo morrer sem except (fechar janela), tenta gravar parado
    def _atexit_mark_stopped() -> None:
        try:
            set_status("parado", last_job="processo encerrado (atexit)")
            add_log("Processo do bot encerrado (atexit)", level="WARN")
        except Exception:
            pass

    atexit.register(_atexit_mark_stopped)

    known = CompanyCheckpoint.load()
    logger.info(f"Empresas já no banco (place_id): {len(known)}")

    if not niche or not cidade:
        logger.info("Montando fila por BAIRRO (cities.json + plano do painel)...")

        niches_path = Path("config/niches.json")
        cities_path = Path("config/cities.json")
        if not niches_path.exists() or not cities_path.exists():
            logger.error("config/niches.json ou config/cities.json ausentes.")
            sys.exit(1)

        try:
            with open(niches_path, "r", encoding="utf-8") as f:
                niches_data = json.load(f).get("nichos", [])
            with open(cities_path, "r", encoding="utf-8") as f:
                cities_data = json.load(f).get("cidades", [])
        except Exception as exc:
            logger.error(f"Falha ao ler JSON: {exc}")
            sys.exit(1)

        # Plano do dashboard (meta de leads + cidades/nichos escolhidos)
        target_leads = 0
        try:
            from src.bot_plan import get_plan, apply_plan_to_job_sources
            plan = get_plan()
            cities_data, niches_data, target_leads = apply_plan_to_job_sources(
                cities_data, niches_data, plan
            )
            logger.info(
                f"Plano painel: meta={target_leads or '∞'} leads | "
                f"cidades={len(cities_data)} | nichos={[n.get('id') for n in niches_data]}"
            )
            add_log(
                f"Plano: meta {target_leads or '∞'} leads | "
                f"{len(cities_data)} cidade(s) | nichos={','.join(n.get('id','') for n in niches_data)}"
            )
        except Exception as exc:
            logger.warning(f"Plano do painel indisponível, usando JSON puro: {exc}")

        from src.coverage import interleave_jobs_by_niche

        jobs = list_pending_jobs(cities_data, niches_data)
        # intercalá nichos só pra variedade na fila — SEM teto por nicho
        jobs = interleave_jobs_by_niche(jobs)
        if limit_jobs and limit_jobs > 0:
            jobs = jobs[:limit_jobs]

        if not jobs:
            logger.warning("Nenhum job pendente. Todas as áreas ativas já foram varridas.")
            set_status("parado", last_job="fila vazia", last_leads=0, session_leads=0)
            add_log("Fila vazia — nada a processar")
            return

        niche_order = []
        _seen_n = set()
        for j in jobs:
            nid = j.get("niche") or ""
            if nid and nid not in _seen_n:
                _seen_n.add(nid)
                niche_order.append(nid)
        per_niche_found: dict[str, int] = {n: 0 for n in niche_order}

        from src.scheduler import LeadGenerationPipeline
        from src.bot_status import (
            get_session_leads,
            set_mission_meta,
            remaining_to_meta,
            should_stop_for_meta,
        )
        pipeline = LeadGenerationPipeline()

        logger.info(
            f"Fila: {len(jobs)} jobs (nicho×cidade×bairro) | "
            f"1º: {jobs[0]['city']}/{jobs[0]['area']}/{jobs[0]['niche']} | "
            f"meta_leads={target_leads or '∞'} | SEM cota por nicho "
            f"(pode encher de qualquer nicho) | meta total Maps+Fonte B"
        )
        add_log(
            f"Sem divisão por nicho — meta total {target_leads or '∞'} "
            f"(Maps+CNPJ joga livre em todos os nichos)"
        )
        set_mission_meta(int(target_leads or 0), reset_leads=False)
        set_status(
            "rodando",
            last_job=f"{jobs[0]['city']}/{jobs[0]['area']}/{jobs[0]['niche']}",
        )
        shared0 = get_session_leads()
        add_log(
            f"Sessão Maps: {len(jobs)} jobs | meta {shared0}/{target_leads or '∞'} "
            f"(Maps+Fonte B) | nichos={','.join(niche_order) or '—'}"
        )

        ran = 0
        total_leads = 0  # só o que ESTE processo Maps achou
        stopped_by_target = False
        try:
            for idx, job in enumerate(jobs, start=1):
                # Só para na META TOTAL (sem cota por nicho)
                if target_leads and should_stop_for_meta():
                    stopped_by_target = True
                    shared = get_session_leads()
                    logger.info(
                        f"🛑 META BATEU {shared}/{target_leads} — Maps PARA "
                        f"(Maps+Fonte B; local maps +{total_leads})."
                    )
                    add_log(f"🛑 META BATEU {shared}/{target_leads} — Maps parando")
                    break

                n_id = job["niche"]
                c_name = job["city"]
                c_state = job["state"]
                area = job["area"]
                q_term = job["query_term"]
                max_r = int(job.get("max_results", 12) or 12)

                # Quanto ainda cabe na meta GLOBAL (ex.: 19/20 → falta 1 → pede 1)
                rem_global = remaining_to_meta()
                if rem_global is not None:
                    if rem_global <= 0:
                        if should_stop_for_meta():
                            stopped_by_target = True
                            break
                        rem_global = 1
                    max_r = max(1, min(max_r, rem_global))

                if is_done(n_id, c_name, c_state, area):
                    logger.info(f"[{idx}/{len(jobs)}] ⏭ já coberto: {c_name}/{area}/{n_id}")
                    continue

                shared = get_session_leads()
                job_label = f"{n_id} | {c_name}/{area}"
                try:
                    logger.info(
                        f"[{idx}/{len(jobs)}] ▶ {n_id} | {c_name}-{c_state} | "
                        f"bairro={area} | pede={max_r} | meta "
                        f"{shared}/{target_leads or '∞'}"
                    )
                    set_status("rodando", last_job=job_label)
                    add_log(
                        f"[{idx}/{len(jobs)}] {job_label} · meta {shared}/{target_leads or '∞'}"
                    )
                    found = pipeline.execute_flow(
                        niche=n_id,
                        city=c_name,
                        state=c_state,
                        query_term=q_term,
                        max_results=max_r,
                        focus_area=area,
                        skip_instagram=True,
                    )
                    mark_done(n_id, c_name, c_state, area, leads_found=found)
                    ran += 1
                    total_leads += int(found or 0)
                    per_niche_found[n_id] = int(per_niche_found.get(n_id, 0) or 0) + int(found or 0)
                    shared_after = get_session_leads()
                    if found:
                        add_log(
                            f"+{found} Maps em {job_label} → meta "
                            f"{shared_after}/{target_leads or '∞'}"
                        )
                    if target_leads and should_stop_for_meta():
                        stopped_by_target = True
                        logger.info(
                            f"🛑 META BATEU {shared_after}/{target_leads} após job — "
                            f"Maps PARA (Fonte B também para)."
                        )
                        add_log(
                            f"🛑 META BATEU {shared_after}/{target_leads} — os 2 param"
                        )
                        break
                except Exception as exc:
                    logger.error(
                        f"Falha {n_id} | {c_name}/{area}: {exc} "
                        f"(área NÃO marcada — retoma depois)"
                    )
                    add_log(f"ERRO {job_label}: {exc}", level="ERROR")

            shared_final = get_session_leads()
            niche_summary = ", ".join(
                f"{n}={per_niche_found.get(n, 0)}" for n in niche_order
            ) or "—"
            end_job = (
                f"maps ok: {ran} áreas, +{total_leads} maps | "
                f"meta {shared_final}/{target_leads or '∞'}"
            )
            if stopped_by_target:
                end_job = (
                    f"meta {shared_final}/{target_leads or '∞'} | "
                    f"maps +{total_leads} | {niche_summary}"
                )
            set_status(
                "parado",
                last_leads=shared_final,
                session_leads=shared_final,
                last_job=end_job,
            )
            add_log(
                f"Maps finalizado: {ran} áreas | +{total_leads} deste processo | "
                f"meta compartilhada {shared_final}/{target_leads or '∞'} | nichos: {niche_summary}"
            )
            logger.info(
                f"Maps ok: {ran} áreas | +{total_leads} Maps | "
                f"meta compartilhada {shared_final}/{target_leads or '∞'} | "
                f"por nicho (maps): {niche_summary}."
            )
        except KeyboardInterrupt:
            shared_final = get_session_leads()
            set_status(
                "parado",
                last_leads=shared_final,
                session_leads=shared_final,
                last_job=f"interrompido: meta {shared_final}/{target_leads or '∞'}",
            )
            add_log(
                f"Interrompido · meta compartilhada {shared_final}/{target_leads or '∞'}",
                level="WARN",
            )
            raise
        except Exception as exc:
            set_status("erro", last_error=str(exc), last_job="falha na sessão")
            add_log(f"Falha da sessão: {exc}", level="ERROR")
            raise
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

            set_status("rodando", last_job=f"{niche} {cidade}", session_leads=0)
            add_log(f"Busca manual: {niche} em {cidade}-{state_val}")
            found = pipeline.execute_flow(
                niche=niche,
                city=cidade,
                state=state_val,
                query_term=query_term,
                max_results=25,
                focus_area=None,
                skip_instagram=True,
            )
            mark_done(niche, cidade, state_val, "_cidade", leads_found=found)
            if found:
                increment_session_leads(found)
            set_status(
                "parado",
                last_leads=found,
                session_leads=found,
                last_job=f"manual {cidade}: {found} leads",
            )
            add_log(f"Manual ok: {found} leads em {cidade}")
        except Exception as exc:
            logger.error(f"Erro ao processar busca manual: {exc}")
            set_status("erro", last_error=str(exc), last_job=f"manual {cidade}")
            add_log(f"Erro manual: {exc}", level="ERROR")
            sys.exit(1)


# ── COMANDO: fonte-b / cnpj (paralelo ao Maps, grátis) ─────────────────────

@cli.command("fonte-b")
@click.option("--cidade", "-c", default=None, help="Cidade única (senão usa plano).")
@click.option("--estado", "-e", default=None, help="UF.")
@click.option("--niche", "-n", default=None, help="Nicho id único.")
@click.option("--meta", default=0, type=int, help="Meta de leads (0 = plano/sem teto).")
def run_fonte_b(
    cidade: str | None,
    estado: str | None,
    niche: str | None,
    meta: int,
) -> None:
    """Fonte B grátis (OSM + CNPJ BrasilAPI se houver tag).

    Só grava SEM site próprio E com telefone ou Instagram.
    Pode rodar em paralelo ao `run` (Maps).
    """
    from src.bot_status import set_status, add_log
    from src.cnpj_source import run_fonte_b_for_plan, run_from_bot_plan

    logger.info("[Fonte B] ========== INÍCIO ==========")
    logger.info(
        "[Fonte B] Só grava se: SEM site próprio e COM telefone ou Instagram."
    )
    try:
        # NÃO zera session_leads — roda em paralelo com o Maps e soma na mesma meta
        set_status("rodando", last_job="fonte-b")
        add_log("Fonte B iniciada (OpenStreetMap + CNPJ se houver)")
    except Exception:
        pass

    try:
        if cidade and niche:
            logger.info(
                "[Fonte B] Modo manual: {} / {} · nicho {}",
                cidade,
                estado or "?",
                niche,
            )
            stats = run_fonte_b_for_plan(
                [{"nome": cidade, "estado": estado or "", "ativo": True}],
                [{"id": niche}],
                target_leads=meta or 0,
                max_per_pair=30,
            )
        else:
            stats = run_from_bot_plan()
        saved = int(stats.get("saved") or 0)
        logger.info(
            "[Fonte B] Resultado: {} lead(s) salvos · {} no mapa · {} sem contato · {} erros",
            saved,
            stats.get("raw_found", 0),
            stats.get("skipped_no_contact", 0),
            stats.get("errors", 0),
        )
        try:
            add_log(
                f"Fonte B fim: {saved} salvos | mapa={stats.get('raw_found')} | "
                f"sem contato={stats.get('skipped_no_contact')}"
            )
            # não sobrescreve contagem da sessão (Maps + Fonte B somados)
            set_status(
                "parado",
                last_job=f"fonte-b: +{saved} leads",
            )
        except Exception:
            pass
    except Exception as exc:
        logger.error("[Fonte B] Falhou: {}", exc)
        try:
            set_status("erro", last_error=str(exc), last_job="fonte-b")
            add_log(f"Fonte B erro: {exc}", level="ERROR")
        except Exception:
            pass
        sys.exit(1)


@cli.command("cnpj")
@click.option("--cidade", "-c", default=None)
@click.option("--estado", "-e", default=None)
@click.option("--niche", "-n", default=None)
@click.option("--meta", default=0, type=int)
def run_cnpj_alias(
    cidade: str | None,
    estado: str | None,
    niche: str | None,
    meta: int,
) -> None:
    """Alias de `fonte-b` (OSM + enriquecimento CNPJ quando houver)."""
    ctx = click.get_current_context()
    ctx.invoke(run_fonte_b, cidade=cidade, estado=estado, niche=niche, meta=meta)


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
