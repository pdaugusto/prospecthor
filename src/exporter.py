"""
exporter.py — Exportação de Leads
=================================

Recupera leads qualificados do banco de dados SQLite e os exporta para
arquivos CSV ou JSON com formatação apropriada.

Nomenclatura padrão dos arquivos:
    leads_{classificacao}_{cidade}_{nicho}_{data}.csv
    Exemplo: leads_quentes_porto_alegre_restaurante_2025-06-18.csv

Campos exportados:
    - Nome da empresa (name)
    - Endereço completo (address)
    - Telefone (phone)
    - Website (website)
    - Instagram (instagram_url)
    - Nota Google (rating)
    - Número de avaliações (review_count)
    - Score total (lead_score)
    - Classificação (lead_class)
    - Problemas encontrados (lead_problems)
    - Serviços sugeridos (lead_services)
    - Prioridade (lead_priority)
    - Data da coleta (created_at)
    - Status (contact_status)

Uso:
    from src.exporter import LeadExporter

    exporter = LeadExporter()
    
    # Exportações em CSV
    exporter.exportar_todos()
    exporter.exportar_quentes()
    exporter.exportar_por_nicho("restaurante")
    exporter.exportar_por_cidade("Porto Alegre")
    
    # Exportação em JSON para API do dashboard
    dados_json = exporter.exportar_para_api()
"""

from __future__ import annotations

import csv
import json
import os
import re
import psycopg2
import psycopg2.extras
import unicodedata
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
_DB_PATH: str = os.getenv("DATABASE_PATH", "data/leads.db")
_EXPORT_DIR: str = os.getenv("EXPORT_DIR", "data/exports/")

# Mapeamento de colunas internas para cabeçalhos amigáveis do arquivo exportado
_CSV_HEADERS = [
    "Nome da empresa",
    "Endereço completo",
    "Telefone",
    "Website",
    "Instagram",
    "Nota Google",
    "Número de avaliações",
    "Score total",
    "Classificação",
    "Problemas encontrados",
    "Serviços sugeridos",
    "Prioridade",
    "Data da coleta",
    "Status"
]


# ---------------------------------------------------------------------------
# Banco de dados e migração de Status de Contato
# ---------------------------------------------------------------------------

class ExporterDatabase:
    """
    Interface com o banco PostgreSQL para leitura e migração de colunas do exportador.
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
        Adiciona a coluna contact_status se ela não existir.
        
        Esta coluna rastreia o andamento comercial do lead:
        'novo', 'contactado', 'convertido' ou 'descartado'.
        """
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'companies' AND column_name = 'contact_status';
            """)
            if not cur.fetchone():
                try:
                    cur.execute(
                        "ALTER TABLE companies ADD COLUMN contact_status TEXT DEFAULT 'novo';"
                    )
                    conn.commit()
                    logger.info("[DB] Coluna 'contact_status' adicionada com sucesso.")
                except Exception as exc:
                    logger.warning(f"[DB] Falha ao adicionar contact_status: {exc}")
            cur.close()
        finally:
            conn.close()

    def query_leads(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """
        Consulta leads com base em filtros aplicados.
        
        Args:
            filters: Dicionário contendo filtros opcionais como:
                     - lead_class (quente/morno/frio)
                     - niche
                     - city
                     - data_inicio (YYYY-MM-DD)
                     - data_fim (YYYY-MM-DD)
        """
        query = "SELECT * FROM companies WHERE lead_score IS NOT NULL"
        params: list[Any] = []
        
        if filters:
            if filters.get("lead_class"):
                query += " AND lead_class = %s"
                params.append(filters["lead_class"])
                
            if filters.get("niche"):
                query += " AND niche = %s"
                params.append(filters["niche"])
                
            if filters.get("city"):
                query += " AND city = %s"
                params.append(filters["city"])
                
            if filters.get("data_inicio"):
                query += " AND created_at::date >= %s::date"
                params.append(filters["data_inicio"])
                
            if filters.get("data_fim"):
                query += " AND created_at::date <= %s::date"
                params.append(filters["data_fim"])
                
        # Ordena leads por maior score primeiro
        query += " ORDER BY lead_score DESC"
        
        conn = self._connect()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(query, params)
            rows = cur.fetchall()
            cur.close()
            return [dict(row) for row in rows]
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Classe Exportadora
# ---------------------------------------------------------------------------

class LeadExporter:
    """
    Manipula e gera arquivos CSV e JSON a partir dos leads qualificados.
    """

    def __init__(self, db_path: str = _DB_PATH, export_dir: str = _EXPORT_DIR) -> None:
        self.db = ExporterDatabase(db_path)
        self.export_dir = Path(export_dir)
        self.export_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Métodos utilitários de limpeza e escrita
    # ------------------------------------------------------------------

    @staticmethod
    def _slugify(text: str) -> str:
        """Normaliza strings para nomenclatura limpa de arquivos."""
        if not text:
            return "todos"
        # Remove acentos
        text = unicodedata.normalize("NFD", text)
        text = "".join(c for c in text if unicodedata.category(c) != "Mn")
        # Substitui espaços e especiais por underscore
        slug = re.sub(r"[^a-zA-Z0-9_]+", "_", text.strip().lower())
        return slug.strip("_")

    def _get_filepath(self, class_val: str, city_val: str, niche_val: str, ext: str) -> Path:
        """Gera o nome de arquivo e caminho absoluto baseado nas variáveis."""
        date_str = datetime.now().strftime("%Y-%m-%d")
        
        c_slug = self._slugify(class_val)
        ct_slug = self._slugify(city_val)
        n_slug = self._slugify(niche_val)
        
        filename = f"leads_{c_slug}_{ct_slug}_{n_slug}_{date_str}.{ext}"
        return self.export_dir / filename

    def _prepare_row(self, lead: dict[str, Any]) -> list[Any]:
        """Formata campos brutos de banco para uma lista alinhada aos headers."""
        # Descompacta listas armazenadas como JSON strings
        try:
            problems_list = json.loads(lead.get("lead_problems") or "[]")
        except Exception:
            problems_list = []
            
        try:
            services_list = json.loads(lead.get("lead_services") or "[]")
        except Exception:
            services_list = []

        problems_str = "; ".join(problems_list)
        services_str = "; ".join(services_list)
        
        # Converte tipo de lead_class para termos amigáveis
        lead_class_map = {
            "raio": "Raio",
            "trovao": "Trovão",
            "eco": "Eco"
        }
        lead_class = lead_class_map.get(lead.get("lead_class", ""), lead.get("lead_class", ""))

        return [
            lead.get("name", ""),
            lead.get("address", ""),
            lead.get("phone", ""),
            lead.get("website", ""),
            lead.get("instagram_url", ""),
            lead.get("rating", ""),
            lead.get("review_count", 0),
            lead.get("lead_score", 0),
            lead_class,
            problems_str,
            services_str,
            lead.get("lead_priority", "").capitalize(),
            lead.get("created_at", ""),
            lead.get("contact_status", "novo").lower()
        ]

    def _write_csv_file(self, leads: list[dict[str, Any]], filepath: Path) -> str:
        """Grava os leads em formato CSV com codificação UTF-8-BOM (compatível com Excel)."""
        try:
            with open(filepath, mode="w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f, delimiter=";")
                # Grava cabeçalhos
                writer.writerow(_CSV_HEADERS)
                # Grava registros
                for lead in leads:
                    writer.writerow(self._prepare_row(lead))
            logger.info(f"[Exporter] Arquivo CSV exportado com sucesso: {filepath.name}")
            return str(filepath.resolve())
        except Exception as exc:
            logger.error(f"[Exporter] Falha ao escrever arquivo CSV {filepath}: {exc}")
            raise

    def _write_json_file(self, leads: list[dict[str, Any]], filepath: Path) -> str:
        """Grava os leads em formato JSON estruturado."""
        try:
            formatted_leads = []
            for lead in leads:
                # Transforma strings JSON em objetos nativos Python para o output JSON
                try:
                    problems = json.loads(lead.get("lead_problems") or "[]")
                except Exception:
                    problems = []
                    
                try:
                    services = json.loads(lead.get("lead_services") or "[]")
                except Exception:
                    services = []
                    
                formatted_leads.append({
                    "name": lead.get("name", ""),
                    "address": lead.get("address", ""),
                    "phone": lead.get("phone", ""),
                    "website": lead.get("website", ""),
                    "instagram": lead.get("instagram_url", ""),
                    "rating": lead.get("rating"),
                    "review_count": lead.get("review_count", 0),
                    "lead_score": lead.get("lead_score", 0),
                    "lead_class": lead.get("lead_class", ""),
                    "lead_problems": problems,
                    "lead_services": services,
                    "lead_priority": lead.get("lead_priority", ""),
                    "created_at": lead.get("created_at", ""),
                    "status": lead.get("contact_status", "novo").lower()
                })
                
            with open(filepath, mode="w", encoding="utf-8") as f:
                json.dump(formatted_leads, f, indent=4, ensure_ascii=False)
            logger.info(f"[Exporter] Arquivo JSON exportado com sucesso: {filepath.name}")
            return str(filepath.resolve())
        except Exception as exc:
            logger.error(f"[Exporter] Falha ao escrever arquivo JSON {filepath}: {exc}")
            raise

    # ------------------------------------------------------------------
    # API Pública de Exportação
    # ------------------------------------------------------------------

    def exportar_todos(self) -> str:
        """Exporta todos os leads qualificados do banco para CSV."""
        leads = self.db.query_leads()
        filepath = self._get_filepath("todos", "todas", "todos", "csv")
        return self._write_csv_file(leads, filepath)

    def exportar_quentes(self) -> str:
        """Exporta apenas leads classificados como 'raio' (55+ pts) para CSV."""
        leads = self.db.query_leads({"lead_class": "raio"})
        filepath = self._get_filepath("raios", "todas", "todos", "csv")
        return self._write_csv_file(leads, filepath)

    def exportar_por_nicho(self, nicho: str) -> str:
        """Exporta leads pertencentes a um nicho específico para CSV."""
        leads = self.db.query_leads({"niche": nicho})
        filepath = self._get_filepath("todos", "todas", nicho, "csv")
        return self._write_csv_file(leads, filepath)

    def exportar_por_cidade(self, cidade: str) -> str:
        """Exporta leads pertencentes a uma cidade específica para CSV."""
        leads = self.db.query_leads({"city": cidade})
        filepath = self._get_filepath("todos", cidade, "todos", "csv")
        return self._write_csv_file(leads, filepath)

    def exportar_por_periodo(self, data_inicio: str, data_fim: str) -> str:
        """
        Exporta leads coletados em um intervalo de datas.
        
        Args:
            data_inicio: Data de corte inicial (formato YYYY-MM-DD).
            data_fim:    Data de corte final (formato YYYY-MM-DD).
        """
        leads = self.db.query_leads({"data_inicio": data_inicio, "data_fim": data_fim})
        filepath = self._get_filepath("periodo", "todas", "todos", "csv")
        return self._write_csv_file(leads, filepath)

    def exportar_para_api(self) -> str:
        """
        Exporta todos os leads estruturados em formato JSON para consumo direto
        do painel/dashboard. Salva o arquivo na pasta de exports e retorna o JSON bruto.
        """
        leads = self.db.query_leads()
        
        # Constrói o caminho do arquivo de persistência física em JSON
        filepath = self._get_filepath("api", "todas", "todos", "json")
        self._write_json_file(leads, filepath)
        
        # Gera o payload de retorno direto
        api_leads = []
        for lead in leads:
            try:
                probs = json.loads(lead.get("lead_problems") or "[]")
            except Exception:
                probs = []
                
            try:
                srvs = json.loads(lead.get("lead_services") or "[]")
            except Exception:
                srvs = []
                
            api_leads.append({
                "id": lead.get("id"),
                "name": lead.get("name", ""),
                "address": lead.get("address", ""),
                "phone": lead.get("phone", ""),
                "website": lead.get("website", ""),
                "instagram": lead.get("instagram_url", ""),
                "rating": lead.get("rating"),
                "review_count": lead.get("review_count", 0),
                "lead_score": lead.get("lead_score", 0),
                "lead_class": lead.get("lead_class", ""),
                "lead_problems": probs,
                "lead_services": srvs,
                "lead_priority": lead.get("lead_priority", ""),
                "created_at": lead.get("created_at", ""),
                "status": lead.get("contact_status", "novo").lower()
            })
            
        return json.dumps(api_leads, ensure_ascii=False)


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

    try:
        exporter = LeadExporter()
        
        # Executa exportações de demonstração
        csv_path = exporter.exportar_todos()
        print(f"\n✓ Leads gerais exportados com sucesso em CSV.")
        print(f"  Caminho: {csv_path}\n")

        json_data = exporter.exportar_para_api()
        # Imprime quantidade de registros no JSON gerado
        count = len(json.loads(json_data))
        print(f"✓ Leads gerais exportados em JSON ({count} registros).")
        print(f"  Consumível pela API do dashboard.")
        
    except FileNotFoundError as e:
        print(f"\n❌ Erro: {e}")
        print("   Execute os checkers e popule o banco antes de exportar.\n")
