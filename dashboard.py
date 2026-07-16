"""
dashboard.py — Servidor Web e API do Prospector Bot
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
from dotenv import load_dotenv

_CSV_HEADERS = ["Nome", "Telefone", "Cidade", "Estado", "Nicho", "Score", "Classificação", "Website", "Instagram", "Problemas"]

load_dotenv()

_DATABASE_URL: str = os.getenv("DATABASE_URL", "")
_DASHBOARD_USER: str = os.getenv("DASHBOARD_USER", "admin")
_DASHBOARD_PASS: str = os.getenv("DASHBOARD_PASS", "senha123")
_SECRET_KEY: str = os.getenv("FLASK_SECRET_KEY", "prospector_secret_key_99812")

app = Flask(__name__)
app.secret_key = _SECRET_KEY

_DASHBOARD_COLUMNS = [
    ("notes", "TEXT"),
    ("contacted_at", "TEXT"),
]


def _run_migrations() -> None:
    if not _DATABASE_URL:
        return
    try:
        conn = psycopg2.connect(_DATABASE_URL)
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id SERIAL PRIMARY KEY,
            place_id TEXT UNIQUE,
            name TEXT NOT NULL,
            category TEXT,
            niche TEXT,
            city TEXT,
            state TEXT,
            address TEXT,
            phone TEXT,
            website TEXT,
            rating REAL,
            review_count INTEGER,
            is_open_now INTEGER,
            opening_hours TEXT,
            latitude REAL,
            longitude REAL,
            maps_url TEXT,
            business_status TEXT,
            source TEXT,
            scraped_at TEXT,
            created_at TEXT DEFAULT (NOW()::TEXT),
            website_status TEXT,
            website_flags TEXT,
            website_mobile INTEGER,
            website_https INTEGER,
            website_speed_s REAL,
            website_score INTEGER,
            website_cms TEXT,
            website_has_contact INTEGER,
            website_title TEXT,
            website_checked_at TEXT,
            instagram_url TEXT,
            instagram_username TEXT,
            instagram_status TEXT,
            instagram_followers INTEGER,
            instagram_following INTEGER,
            instagram_posts INTEGER,
            instagram_last_post TEXT,
            instagram_has_bio INTEGER,
            instagram_has_link INTEGER,
            instagram_bio TEXT,
            instagram_is_verified INTEGER,
            instagram_is_business INTEGER,
            instagram_score INTEGER,
            instagram_checked_at TEXT,
            menu_google INTEGER,
            menu_instagram INTEGER,
            menu_apps TEXT,
            menu_site INTEGER,
            menu_status TEXT,
            menu_score INTEGER,
            menu_checked_at TEXT,
            lead_score INTEGER,
            lead_class TEXT,
            lead_problems TEXT,
            lead_services TEXT,
            lead_priority TEXT,
            scored_at TEXT,
            notes TEXT,
            contacted_at TEXT
        );
        """)
        conn.commit()
        for col_name, col_type in _DASHBOARD_COLUMNS:
            cur.execute("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'companies' AND column_name = %s;
            """, (col_name,))
            if not cur.fetchone():
                cur.execute(f"ALTER TABLE companies ADD COLUMN {col_name} {col_type};")
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


_run_migrations()

_MOCK_LEADS = [
    {
        "id": 1001, "name": "Restaurante Sabor Caseiro", "category": "Restaurante",
        "niche": "restaurante", "city": "Porto Alegre", "state": "RS",
        "address": "Rua das Flores, 123 - Centro", "phone": "(51) 99999-1234",
        "website": "", "rating": 3.8, "review_count": 12,
        "maps_url": "https://maps.google.com/?q=Restaurante+Sabor+Caseiro+Porto+Alegre",
        "website_status": "sem_site", "instagram_status": "tem_instagram",
        "instagram_username": "saborcaseiro_poa",
        "instagram_url": "https://www.instagram.com/saborcaseiro_poa/",
        "instagram_has_link": 0, "lead_score": 70, "lead_class": "raio",
        "lead_priority": "alta",
        "lead_problems": json.dumps(["Sem site (+55)", "Instagram sem link na bio (+15)"], ensure_ascii=False),
        "lead_services": json.dumps(["Site profissional", "Conectar Instagram ao site"], ensure_ascii=False),
        "contacted_at": None, "notes": "", "created_at": (datetime.now() - timedelta(hours=3)).isoformat()
    },
    {
        "id": 1002, "name": "Barbearia Estilo Real", "category": "Barbearia",
        "niche": "barbearia", "city": "Porto Alegre", "state": "RS",
        "address": "Av. Ipiranga, 4550", "phone": "(51) 98888-5678",
        "website": "http://barbeariaestiloreal.wixsite.com/home", "rating": 4.2, "review_count": 8,
        "maps_url": "https://maps.google.com/?q=Barbearia+Estilo+Real+Porto+Alegre",
        "website_status": "template_generico", "website_mobile": 0, "website_https": 0,
        "instagram_status": "tem_instagram", "instagram_username": "estiloreal_barber",
        "instagram_url": "https://www.instagram.com/estiloreal_barber/",
        "instagram_has_link": 1, "lead_score": 30, "lead_class": "trovao",
        "lead_priority": "media",
        "lead_problems": json.dumps(["Site sem HTTPS (+15)", "Site não mobile-friendly (+15)"], ensure_ascii=False),
        "lead_services": json.dumps(["Certificado SSL", "Design responsivo"], ensure_ascii=False),
        "contacted_at": datetime.now().isoformat(), "notes": "", "created_at": (datetime.now() - timedelta(days=2)).isoformat()
    },
    {
        "id": 1003, "name": "Clínica Sorriso Lindo", "category": "Consultório Odontológico",
        "niche": "odontologia", "city": "Caxias do Sul", "state": "RS",
        "address": "Rua Sinimbu, 1800", "phone": "(54) 3222-9900",
        "website": "https://www.sorrisolindo.com.br", "rating": 4.9, "review_count": 98,
        "maps_url": "", "website_status": "ok", "website_mobile": 1, "website_https": 1,
        "instagram_status": "tem_instagram", "instagram_username": "sorrisolindo_odonto",
        "instagram_url": "https://www.instagram.com/sorrisolindo_odonto/",
        "instagram_has_link": 1, "lead_score": 0, "lead_class": "eco",
        "lead_priority": "baixa",
        "lead_problems": json.dumps([], ensure_ascii=False),
        "lead_services": json.dumps([], ensure_ascii=False),
        "contacted_at": None, "notes": "", "created_at": (datetime.now() - timedelta(days=5)).isoformat()
    },
    {
        "id": 1004, "name": "Pizzaria da Mamma", "category": "Pizzaria",
        "niche": "restaurante", "city": "Canoas", "state": "RS",
        "address": "Av. Boqueirão, 1020", "phone": "(51) 97777-4321",
        "website": "https://pizzariadamamma.com.br", "rating": 4.1, "review_count": 45,
        "maps_url": "", "website_status": "ok", "website_mobile": 1, "website_https": 0,
        "instagram_status": "tem_instagram", "instagram_username": "pizzaria_da_mamma",
        "instagram_url": "https://www.instagram.com/pizzaria_da_mamma/",
        "instagram_has_link": 0, "lead_score": 30, "lead_class": "trovao",
        "lead_priority": "media",
        "lead_problems": json.dumps(["Site sem HTTPS (+15)", "Instagram sem link (+15)"], ensure_ascii=False),
        "lead_services": json.dumps(["Certificado SSL", "Conectar Instagram ao site"], ensure_ascii=False),
        "contacted_at": None, "notes": "", "created_at": (datetime.now() - timedelta(days=1)).isoformat()
    }
]


def _get_db_connection():
    return psycopg2.connect(_DATABASE_URL)


_SOCIAL_MARKERS = (
    "instagram.com", "facebook.com", "fb.com", "linktr.ee",
    "bio.link", "wa.me", "whatsapp.com", "tiktok.com",
)

# Pré-filtro SQL: candidatos a Raio (sem site / só social / classificados raio).
# O corte final (sem site próprio) é feito em _is_raio_lead.
_SQL_RAIO_LEADS = """
SELECT * FROM companies
WHERE website_status IN ('sem_site', 'so_social')
   OR website IS NULL
   OR TRIM(COALESCE(website, '')) = ''
   OR lead_class = 'raio'
   OR website ILIKE '%%instagram.com%%'
   OR website ILIKE '%%facebook.com%%'
   OR website ILIKE '%%linktr.ee%%'
ORDER BY lead_score DESC NULLS LAST;
"""


def _is_raio_lead(lead: dict[str, Any]) -> bool:
    """True se a empresa não tem site próprio (oportunidade Raio)."""
    status = (lead.get("website_status") or "").strip().lower()
    if status in ("sem_site", "so_social"):
        return True
    website = (lead.get("website") or "").strip()
    if not website:
        return True
    lower = website.lower()
    if any(m in lower for m in _SOCIAL_MARKERS):
        return True
    return False


def _get_all_leads():
    """Retorna apenas leads ⚡ Raio — empresas sem site próprio."""
    if not _DATABASE_URL:
        return [l for l in _MOCK_LEADS if _is_raio_lead(l)]
    try:
        conn = _get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(_SQL_RAIO_LEADS)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        if not rows:
            return [l for l in _MOCK_LEADS if _is_raio_lead(l)]
        return [dict(row) for row in rows if _is_raio_lead(dict(row))]
    except Exception:
        return [l for l in _MOCK_LEADS if _is_raio_lead(l)]


def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        if request.form.get("username") == _DASHBOARD_USER and request.form.get("password") == _DASHBOARD_PASS:
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        error = "Usuário ou senha incorretos."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/")
@app.route("/leads")
@app.route("/lead/<int:lead_id>")
@app.route("/reports")
@app.route("/settings")
@login_required
def dashboard(lead_id=None):
    return render_template("index.html")


@app.route("/api/leads")
@login_required
def api_leads():
    return jsonify(_get_all_leads())


@app.route("/api/leads/<int:lead_id>")
@login_required
def api_lead_detail(lead_id):
    leads = _get_all_leads()
    lead = next((l for l in leads if l["id"] == lead_id), None)
    if not lead:
        return jsonify({"error": "Not found"}), 404
    return jsonify(lead)


@app.route("/api/leads/<int:lead_id>/status", methods=["PUT"])
@login_required
def api_update_status(lead_id):
    data = request.get_json() or {}
    if _DATABASE_URL:
        try:
            conn = _get_db_connection()
            cur = conn.cursor()
            now = datetime.now().isoformat()
            if "status" in data:
                cur.execute("UPDATE companies SET contacted_at = %s WHERE id = %s", (now, lead_id))
            if "notes" in data:
                cur.execute("UPDATE companies SET notes = %s WHERE id = %s", (data["notes"], lead_id))
            conn.commit()
            cur.close()
            conn.close()
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify({"success": True})


@app.route("/api/stats")
@login_required
def api_stats():
    # Já vem filtrado: só ⚡ Raio (sem site)
    leads = _get_all_leads()
    nichos = {}
    for l in leads:
        n = l.get("niche") or "outro"
        nichos[n] = nichos.get(n, 0) + 1
    contactados = len([l for l in leads if l.get("contacted_at")])
    descartados = len([
        l for l in leads
        if (l.get("notes") or "").lower() == "descartado"
        or (l.get("contact_status") or "").lower() == "descartado"
    ])
    novos = len(leads) - contactados  # aproximação: não contactados
    return jsonify({
        "total": len(leads),
        "quentes": len(leads),  # todos listados são Raio
        "mornos": contactados,  # reutilizado no UI como "Contactados"
        "frios": descartados,   # reutilizado no UI como "Descartados"
        "novos": max(novos, 0),
        "descartados": descartados,
        "nichos": nichos
    })


@app.route("/api/reports/<period>")
@login_required
def api_reports(period):
    leads = _get_all_leads()
    days = 1 if period == "daily" else 7 if period == "weekly" else 30
    cutoff = datetime.now() - timedelta(days=days)
    filtered = []
    for l in leads:
        try:
            created = datetime.fromisoformat(l.get("created_at", "").replace("Z", "").split("+")[0])
            if created >= cutoff:
                filtered.append(l)
        except Exception:
            filtered.append(l)
    problemas = {}
    for l in filtered:
        try:
            for p in json.loads(l.get("lead_problems") or "[]"):
                k = p.split(" (")[0]
                problemas[k] = problemas.get(k, 0) + 1
        except Exception:
            pass
    total = len(filtered)
    contactados = len([l for l in filtered if l.get("contacted_at")])
    convertidos = len([l for l in filtered if l.get("notes") == "Convertido"])
    return jsonify({
        "total": total,
        "quentes": len([l for l in filtered if l.get("lead_class") == "raio"]),
        "mornos": len([l for l in filtered if l.get("lead_class") == "trovao"]),
        "frios": len([l for l in filtered if l.get("lead_class") == "eco"]),
        "conversao_taxa": round((convertidos / max(contactados, 1)) * 100, 1),
        "problemas_comuns": problemas
    })


@app.route("/api/export/csv")
@login_required
def api_export_csv():
    leads = _get_all_leads()
    output = io.StringIO()
    output.write("\ufeff")
    w = csv.writer(output, delimiter=";")
    w.writerow(_CSV_HEADERS)
    for l in leads:
        w.writerow([
            l.get("name", ""),
            l.get("phone", ""),
            l.get("city", ""),
            l.get("state", ""),
            l.get("niche", ""),
            l.get("lead_score", 0),
            l.get("lead_class", ""),
            l.get("website", ""),
            l.get("instagram_url", ""),
            l.get("lead_problems", "")
        ])
    resp = make_response(output.getvalue())
    resp.headers["Content-Disposition"] = f"attachment; filename=leads_{datetime.now().strftime('%Y-%m-%d')}.csv"
    resp.headers["Content-type"] = "text/csv; charset=utf-8"
    return resp


@app.route("/api/settings")
@login_required
def api_settings():
    return jsonify({"niches": [], "cities": []})


if __name__ == "__main__":
    Path("templates").mkdir(exist_ok=True)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)