"""
dashboard.py — Servidor Web e API do Prospector Bot
===================================================

Gerencia a interface administrativa e relatórios de prospecção.
Fornece endpoints JSON e renderiza a interface em Single Page Application (SPA).

Uso:
    python dashboard.py
    Acesse http://localhost:5000 no seu navegador.
"""

from __future__ import annotations

import csv
import io
import json
import os
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    Response,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from loguru import logger
from dotenv import load_dotenv

# Importa o exportador implementado e seus cabeçalhos
from src.exporter import LeadExporter, _CSV_HEADERS

# ---------------------------------------------------------------------------
# Configurações do Servidor
# ---------------------------------------------------------------------------

load_dotenv()

_DATABASE_URL: str = os.getenv("DATABASE_URL", "")
_DB_PATH: str = os.getenv("DATABASE_PATH", "data/leads.db")  # mantido para compatibilidade com main.py
_DASHBOARD_USER: str = os.getenv("DASHBOARD_USER", "admin")
_DASHBOARD_PASS: str = os.getenv("DASHBOARD_PASS", "senha123")
_SECRET_KEY: str = os.getenv("FLASK_SECRET_KEY", "prospector_secret_key_99812")

app = Flask(__name__)
app.secret_key = _SECRET_KEY

# ---------------------------------------------------------------------------
# Migrações adicionais para notas e histórico no SQLite
# ---------------------------------------------------------------------------

_DASHBOARD_COLUMNS = [
    ("notes",        "TEXT"),     # Anotações do lead
    ("contacted_at",  "TEXT"),     # Timestamp de quando o status mudou para contactado/convertido
]

def _run_migrations() -> None:
    """Cria tabela e adiciona colunas de notas e histórico no PostgreSQL."""
    if not _DATABASE_URL:
        logger.warning("[Dashboard Migrações] DATABASE_URL não configurada — pulando migrações.")
        return
    try:
        conn = psycopg2.connect(_DATABASE_URL)
        cur = conn.cursor()
        # Cria a tabela caso esteja iniciando do zero
        cur.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id                  SERIAL PRIMARY KEY,
            place_id            TEXT UNIQUE,
            name                TEXT NOT NULL,
            category            TEXT,
            niche               TEXT,
            city                TEXT,
            state               TEXT,
            address             TEXT,
            phone               TEXT,
            website             TEXT,
            rating              REAL,
            review_count        INTEGER,
            is_open_now         INTEGER,
            opening_hours       TEXT,
            latitude            REAL,
            longitude           REAL,
            maps_url            TEXT,
            business_status     TEXT,
            source              TEXT,
            scraped_at          TEXT NOT NULL,
            created_at          TEXT NOT NULL DEFAULT (NOW()::TEXT),
            website_status      TEXT,
            website_flags       TEXT,
            website_mobile      INTEGER,
            website_https       INTEGER,
            website_speed_s     REAL,
            website_score       INTEGER,
            website_cms         TEXT,
            website_has_contact INTEGER,
            website_title       TEXT,
            website_checked_at  TEXT,
            instagram_url       TEXT,
            instagram_username  TEXT,
            instagram_status    TEXT,
            instagram_followers INTEGER,
            instagram_following INTEGER,
            instagram_posts      INTEGER,
            instagram_last_post  TEXT,
            instagram_has_bio    INTEGER,
            instagram_has_link   INTEGER,
            instagram_bio        TEXT,
            instagram_is_verified INTEGER,
            instagram_is_business INTEGER,
            instagram_score      INTEGER,
            instagram_checked_at TEXT,
            menu_google         INTEGER,
            menu_instagram      INTEGER,
            menu_apps           TEXT,
            menu_site           INTEGER,
            menu_status         TEXT,
            menu_score          INTEGER,
            menu_checked_at     TEXT,
            lead_score          INTEGER,
            lead_class          TEXT,
            lead_problems       TEXT,
            lead_services       TEXT,
            lead_priority       TEXT,
            scored_at           TEXT,
            contact_status      TEXT DEFAULT 'novo'
        );
        """)
        conn.commit()

        # Adiciona colunas do dashboard se necessário (via information_schema)
        added = []
        for col_name, col_type in _DASHBOARD_COLUMNS:
            cur.execute("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'companies' AND column_name = %s;
            """, (col_name,))
            if not cur.fetchone():
                cur.execute(f"ALTER TABLE companies ADD COLUMN {col_name} {col_type};")
                added.append(col_name)
        if added:
            conn.commit()
            logger.info(f"[Dashboard Migrações] Adicionou colunas: {added}")
        cur.close()
        conn.close()
    except Exception as exc:
        logger.error(f"[Dashboard Migrações] Falha no setup do banco: {exc}")

# Executa migrações no startup
_run_migrations()


# ---------------------------------------------------------------------------
# Dados de Exemplo Fictícios (Mock)
# ---------------------------------------------------------------------------

_MOCK_LEADS = [
    {
        "id": 1001,
        "name": "Restaurante Sabor Caseiro",
        "category": "Restaurante",
        "niche": "restaurante",
        "city": "Porto Alegre",
        "state": "RS",
        "address": "Rua das Flores, 123 - Centro",
        "phone": "(51) 99999-1234",
        "website": "",
        "rating": 3.8,
        "review_count": 12,
        "maps_url": "https://maps.google.com/?q=Restaurante+Sabor+Caseiro+Porto+Alegre",
        "website_status": "sem_site",
        "instagram_status": "tem_instagram",
        "instagram_username": "saborcaseiro_poa",
        "instagram_url": "https://www.instagram.com/saborcaseiro_poa/",
        "instagram_has_link": 0,
        "lead_score": 55,
        "lead_class": "raio",
        "lead_priority": "alta",
        "lead_problems": json.dumps(["Sem site (+40)", "Tem Instagram mas sem link na bio (+15)"], ensure_ascii=False),
        "lead_services": json.dumps(["Site profissional", "Conectar Instagram ao site"], ensure_ascii=False),
        "contact_status": "novo",
        "notes": "Empresa tradicional no centro, sem site próprio nem link no Instagram. Grande potencial para fechamento de site e integração.",
        "created_at": (datetime.now() - timedelta(hours=3)).isoformat()
    },
    {
        "id": 1002,
        "name": "Barbearia Estilo Real",
        "category": "Barbearia",
        "niche": "barbearia",
        "city": "Porto Alegre",
        "state": "RS",
        "address": "Av. Ipiranga, 4550 - Jardim Botânico",
        "phone": "(51) 98888-5678",
        "website": "http://barbeariaestiloreal.wixsite.com/home",
        "rating": 4.2,
        "review_count": 8,
        "maps_url": "https://maps.google.com/?q=Barbearia+Estilo+Real+Porto+Alegre",
        "website_status": "template_generico",
        "website_mobile": 0,
        "website_https": 0,
        "instagram_status": "tem_instagram",
        "instagram_username": "estiloreal_barber",
        "instagram_url": "https://www.instagram.com/estiloreal_barber/",
        "instagram_has_link": 1,
        "lead_score": 30,
        "lead_class": "trovao",
        "lead_priority": "media",
        "lead_problems": json.dumps(["Site sem HTTPS (+15)", "Site não mobile-friendly (+15)"], ensure_ascii=False),
        "lead_services": json.dumps(["Certificado SSL", "Design responsivo"], ensure_ascii=False),
        "contact_status": "contactado",
        "notes": "Entrei em contato com o sócio Carlos via WhatsApp. Ele disse que o site antigo foi feito por um amigo no Wix, não é seguro nem responsivo e precisa de correção.",
        "created_at": (datetime.now() - timedelta(days=2)).isoformat()
    },
    {
        "id": 1003,
        "name": "Clínica Sorriso Lindo",
        "category": "Consultório Odontológico",
        "niche": "clinica",
        "city": "Caxias do Sul",
        "state": "RS",
        "address": "Rua Sinimbu, 1800 - Centro",
        "phone": "(54) 3222-9900",
        "website": "https://www.sorrisolindo.com.br",
        "rating": 4.9,
        "review_count": 98,
        "maps_url": "https://maps.google.com/?q=Clinica+Sorriso+Lindo+Caxias+do+Sul",
        "website_status": "ok",
        "website_mobile": 1,
        "website_https": 1,
        "instagram_status": "tem_instagram",
        "instagram_username": "sorrisolindo_odonto",
        "instagram_url": "https://www.instagram.com/sorrisolindo_odonto/",
        "instagram_has_link": 1,
        "lead_score": 0,
        "lead_class": "eco",
        "lead_priority": "baixa",
        "lead_problems": json.dumps([], ensure_ascii=False),
        "lead_services": json.dumps([], ensure_ascii=False),
        "contact_status": "novo",
        "notes": "Presença digital muito forte, site moderno e rápido, redes sociais super ativas. Não há oportunidade viável de venda no momento.",
        "created_at": (datetime.now() - timedelta(days=5)).isoformat()
    },
    {
        "id": 1004,
        "name": "Pizzaria da Mamma",
        "category": "Pizzaria",
        "niche": "pizzaria",
        "city": "Canoas",
        "state": "RS",
        "address": "Av. Boqueirão, 1020 - Marechal Rondon",
        "phone": "(51) 97777-4321",
        "website": "https://pizzariadamamma.com.br",
        "rating": 4.1,
        "review_count": 45,
        "maps_url": "https://maps.google.com/?q=Pizzaria+da+Mamma+Canoas",
        "website_status": "ok",
        "website_mobile": 1,
        "website_https": 0,
        "instagram_status": "tem_instagram",
        "instagram_username": "pizzaria_da_mamma",
        "instagram_url": "https://www.instagram.com/pizzaria_da_mamma/",
        "instagram_has_link": 0,
        "lead_score": 30,
        "lead_class": "trovao",
        "lead_priority": "media",
        "lead_problems": json.dumps(["Site sem HTTPS (+15)", "Tem Instagram mas sem link na bio (+15)"], ensure_ascii=False),
        "lead_services": json.dumps(["Certificado SSL", "Conectar Instagram ao site"], ensure_ascii=False),
        "contact_status": "novo",
        "notes": "O restaurante está vendendo somente via iFood e Insta sem link. Uma proposta de site profissional com HTTPS resolveria o problema.",
        "created_at": (datetime.now() - timedelta(days=1)).isoformat()
    }
]

# ---------------------------------------------------------------------------
# Funções do Banco de Dados
# ---------------------------------------------------------------------------

def _get_db_connection():
    """Retorna uma conexão psycopg2 ao PostgreSQL via DATABASE_URL."""
    return psycopg2.connect(_DATABASE_URL)

def _get_all_leads() -> list[dict[str, Any]]:
    """Carrega dados reais do banco. Se estiver vazio ou DATABASE_URL ausente, retorna mock data."""
    if not _DATABASE_URL:
        return _MOCK_LEADS
    try:
        conn = _get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM companies ORDER BY lead_score DESC;")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        if not rows:
            return _MOCK_LEADS
        return [dict(row) for row in rows]
    except Exception as exc:
        logger.warning(f"[Dashboard] Falha ao carregar PostgreSQL ({exc}). Usando dados fictícios.")
        return _MOCK_LEADS


# ---------------------------------------------------------------------------
# Middlewares de Autenticação
# ---------------------------------------------------------------------------

def login_required(f):
    """Decorator para exigir login nas rotas do Flask."""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


# ---------------------------------------------------------------------------
# Rotas de Autenticação e Navegação
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login() -> Any:
    """Tela de login."""
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))
        
    error = None
    if request.method == "POST":
        user = request.form.get("username")
        password = request.form.get("password")
        if user == _DASHBOARD_USER and password == _DASHBOARD_PASS:
            session["logged_in"] = True
            session.permanent = True
            logger.info(f"[Dashboard] Login bem-sucedido para o usuário: {user}")
            return redirect(url_for("dashboard"))
        else:
            error = "Usuário ou senha incorretos."
            
    # Renderiza HTML inline do login para evitar arquivos extras
    return render_template("login.html", error=error)

@app.route("/logout")
def logout() -> Any:
    """Desloga o usuário."""
    session.clear()
    return redirect(url_for("login"))

# Rota curinga para servir o SPA do Dashboard
@app.route("/")
@app.route("/leads")
@app.route("/lead/<int:lead_id>")
@app.route("/reports")
@app.route("/settings")
@login_required
def dashboard(lead_id: int | None = None) -> Any:
    """Renderiza a Single Page Application (SPA) do Prospector Bot."""
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Endpoints da API JSON
# ---------------------------------------------------------------------------

@app.route("/api/leads", methods=["GET"])
@login_required
def api_leads() -> Response:
    """Retorna lista de todos os leads cadastrados."""
    leads = _get_all_leads()
    return jsonify(leads)

@app.route("/api/leads/quentes", methods=["GET"])
@login_required
def api_leads_quentes() -> Response:
    """Retorna apenas leads da categoria Raio (leads prioritários)."""
    leads = [l for l in _get_all_leads() if l.get("lead_class") == "raio"]
    return jsonify(leads)

@app.route("/api/leads/mornos", methods=["GET"])
@login_required
def api_leads_mornos() -> Response:
    """Retorna apenas leads da categoria Trovão (leads intermediários)."""
    leads = [l for l in _get_all_leads() if l.get("lead_class") == "trovao"]
    return jsonify(leads)

@app.route("/api/leads/<int:lead_id>", methods=["GET"])
@login_required
def api_lead_detail(lead_id: int) -> Response:
    """Retorna dados detalhados de um lead por ID."""
    leads = _get_all_leads()
    lead = next((l for l in leads if l["id"] == lead_id), None)
    if not lead:
        return jsonify({"error": "Lead não encontrado"}), 404
    return jsonify(lead)

@app.route("/api/leads/<int:lead_id>/status", methods=["PUT"])
@login_required
def api_update_lead_status(lead_id: int) -> Response:
    """
    Atualiza o contact_status (novo/contactado/convertido/descartado)
    e opcionalmente as anotações (notes) do lead.
    """
    data = request.get_json() or {}
    status = data.get("status")
    notes = data.get("notes")

    # Tenta atualizar no PostgreSQL
    if _DATABASE_URL:
        try:
            conn = _get_db_connection()
            cur = conn.cursor()
            # Verifica se registro existe
            cur.execute("SELECT 1 FROM companies WHERE id = %s LIMIT 1", (lead_id,))
            exists = cur.fetchone()

            if exists:
                now = datetime.now().isoformat()
                if status and notes is not None:
                    cur.execute(
                        "UPDATE companies SET contact_status = %s, notes = %s, contacted_at = %s WHERE id = %s",
                        (status, notes, now, lead_id)
                    )
                elif status:
                    cur.execute(
                        "UPDATE companies SET contact_status = %s, contacted_at = %s WHERE id = %s",
                        (status, now, lead_id)
                    )
                elif notes is not None:
                    cur.execute("UPDATE companies SET notes = %s WHERE id = %s", (notes, lead_id))
                conn.commit()
                cur.close()
                conn.close()
                logger.info(f"[Dashboard] Lead {lead_id} atualizado (status={status}, notes={notes is not None})")
                return jsonify({"success": True})
            cur.close()
            conn.close()
        except Exception as exc:
            logger.warning(f"[Dashboard API] PostgreSQL update failed ({exc}), simulando em mock.")

    # Fallback: mock memory update
    lead = next((l for l in _MOCK_LEADS if l["id"] == lead_id), None)
    if lead:
        if status:
            lead["contact_status"] = status
            lead["contacted_at"] = datetime.now().isoformat()
        if notes is not None:
            lead["notes"] = notes
        return jsonify({"success": True, "simulated": True})

    return jsonify({"error": "Lead não encontrado"}), 404

@app.route("/api/stats", methods=["GET"])
@login_required
def api_stats() -> Response:
    """Gera contadores estatísticos gerais de presença digital dos leads."""
    leads = _get_all_leads()
    
    total = len(leads)
    quentes = len([l for l in leads if l.get("lead_class") == "raio"])
    mornos = len([l for l in leads if l.get("lead_class") == "trovao"])
    frios = len([l for l in leads if l.get("lead_class") == "eco"])
    descartados = 0
    
    # Simula contagem de novos hoje (criados nas últimas 24h)
    novos_hoje = len([
        l for l in leads
        if l.get("created_at") and
        (datetime.now() - datetime.fromisoformat(l["created_at"].replace("Z", "+00:00").split("+")[0])).days == 0
    ])

    # Distribuição por nicho
    nichos: dict[str, int] = {}
    for l in leads:
        n = l.get("niche") or "outro"
        nichos[n] = nichos.get(n, 0) + 1

    return jsonify({
        "total": total,
        "quentes": quentes,
        "mornos": mornos,
        "frios": frios,
        "descartados": descartados,
        "novos_hoje": novos_hoje,
        "nichos": nichos
    })

@app.route("/api/reports/<string:period>", methods=["GET"])
@login_required
def api_reports(period: str) -> Response:
    """
    Retorna métricas consolidadas por período: daily, weekly ou monthly.
    """
    leads = _get_all_leads()
    days_limit = 1 if period == "daily" else 7 if period == "weekly" else 30
    
    cutoff = datetime.now() - timedelta(days=days_limit)
    filtered = []
    for l in leads:
        try:
            created = datetime.fromisoformat(l["created_at"].replace("Z", "+00:00").split("+")[0])
            if created >= cutoff:
                filtered.append(l)
        except Exception:
            filtered.append(l)  # fallback se data não estiver em formato iso

    total = len(filtered)
    quentes = len([l for l in filtered if l.get("lead_class") == "raio"])
    mornos = len([l for l in filtered if l.get("lead_class") == "trovao"])
    frios = len([l for l in filtered if l.get("lead_class") == "eco"])
    
    # Mapeia problemas comuns
    problemas: dict[str, int] = {}
    for l in filtered:
        try:
            probs = json.loads(l.get("lead_problems") or "[]")
            for p in probs:
                p_clean = p.split(" (")[0]
                problemas[p_clean] = problemas.get(p_clean, 0) + 1
        except Exception:
            pass

    return jsonify({
        "period": period,
        "total": total,
        "quentes": quentes,
        "mornos": mornos,
        "frios": frios,
        "conversao_taxa": round((len([l for l in filtered if l.get("contact_status") == "convertido"]) / max(total, 1)) * 100, 1),
        "problemas_comuns": problemas
    })

@app.route("/api/export/csv", methods=["GET"])
@login_required
def api_export_csv() -> Any:
    """Gera dinamicamente o arquivo CSV de todos os leads qualificados e inicia o download."""
    leads = _get_all_leads()
    
    # Cria buffer em memória
    output = io.StringIO()
    # UTF-8 com BOM para Excel reconhecer acentuação
    output.write("\ufeff")
    
    writer = csv.writer(output, delimiter=";")
    writer.writerow(_CSV_HEADERS)
    
    exporter = LeadExporter()
    for lead in leads:
        writer.writerow(exporter._prepare_row(lead))
        
    response = make_response(output.getvalue())
    date_str = datetime.now().strftime("%Y-%m-%d")
    response.headers["Content-Disposition"] = f"attachment; filename=leads_export_{date_str}.csv"
    response.headers["Content-type"] = "text/csv; charset=utf-8-sig"
    return response

@app.route("/api/settings", methods=["GET", "POST"])
@login_required
def api_settings() -> Response:
    """GET para ler e POST para salvar configurações de nichos e cidades do bot."""
    niches_path = Path("config/niches.json")
    cities_path = Path("config/cities.json")

    if request.method == "POST":
        data = request.get_json() or {}
        
        # Grava nichos
        if "nichos" in data:
            try:
                with open(niches_path, "w", encoding="utf-8") as f:
                    json.dump({"nichos": data["nichos"]}, f, indent=2, ensure_ascii=False)
            except Exception as exc:
                return jsonify({"error": f"Falha ao salvar nichos: {exc}"}), 500
                
        # Grava cidades
        if "cidades" in data:
            try:
                with open(cities_path, "w", encoding="utf-8") as f:
                    json.dump({"cidades": data["cidades"]}, f, indent=2, ensure_ascii=False)
            except Exception as exc:
                return jsonify({"error": f"Falha ao salvar cidades: {exc}"}), 500
                
        logger.info("[Dashboard] Configurações salvas pelo usuário.")
        return jsonify({"success": True})

    # GET: Lê as configurações
    niches = {"nichos": []}
    cities = {"cidades": []}
    
    if niches_path.exists():
        try:
            with open(niches_path, "r", encoding="utf-8") as f:
                niches = json.load(f)
        except Exception:
            pass
            
    if cities_path.exists():
        try:
            with open(cities_path, "r", encoding="utf-8") as f:
                cities = json.load(f)
        except Exception:
            pass

    return jsonify({
        "niches": niches.get("nichos", []),
        "cities": cities.get("cidades", []),
        "api_key_configured": bool(os.getenv("GOOGLE_MAPS_API_KEY"))
    })


# ---------------------------------------------------------------------------
# Execução do Servidor
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Garante que os templates existam
    Path("templates").mkdir(exist_ok=True)
    
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Iniciando Dashboard Web do Prospector Bot na porta {port}...")
    app.run(host="0.0.0.0", port=port)
