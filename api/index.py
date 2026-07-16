import sys
import os
import io
import csv
import json
import time
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template, request, make_response, session, redirect, url_for

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

app = Flask(__name__, template_folder="../templates")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "prospector_secret")

DATABASE_URL = os.getenv("DATABASE_URL", "")
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASS", "senha123")

_SOCIAL_MARKERS = (
    "instagram.com", "facebook.com", "fb.com", "linktr.ee",
    "bio.link", "wa.me", "whatsapp.com", "tiktok.com",
)

# Colunas leves para a listagem (menos payload na Vercel)
_LIST_COLS = """
    id, name, phone, city, state, niche, category, address,
    website, website_status, maps_url, rating, review_count,
    instagram_url, instagram_username, lead_score, lead_class,
    lead_problems, lead_services, lead_priority,
    contacted_at, notes, created_at, scraped_at
"""

_SQL_RAIO_LEADS = f"""
SELECT {_LIST_COLS}
FROM companies
WHERE website_status IN ('sem_site', 'so_social')
   OR website IS NULL
   OR TRIM(COALESCE(website, '')) = ''
   OR lead_class = 'raio'
   OR website ILIKE '%%instagram.com%%'
   OR website ILIKE '%%facebook.com%%'
   OR website ILIKE '%%linktr.ee%%'
ORDER BY lead_score DESC NULLS LAST;
"""

_SQL_LEAD_BY_ID = f"""
SELECT {_LIST_COLS}
FROM companies
WHERE id = %s
LIMIT 1;
"""

# Cache em memória (processo serverless reutiliza warm instances)
_cache = {"leads": None, "leads_at": 0, "stats": None, "stats_at": 0}
_CACHE_TTL = 45  # segundos


def get_db():
    return psycopg2.connect(DATABASE_URL)


def _is_raio_lead(lead):
    """True se a empresa não tem site próprio."""
    status = (lead.get("website_status") or "").strip().lower()
    if status in ("sem_site", "so_social"):
        return True
    website = (lead.get("website") or "").strip()
    if not website:
        return True
    lower = website.lower()
    return any(m in lower for m in _SOCIAL_MARKERS)


def get_all_leads(use_cache=True):
    """Lista só leads Raio (sem site). Cache curto para menos carga no Supabase."""
    now = time.time()
    if use_cache and _cache["leads"] is not None and (now - _cache["leads_at"]) < _CACHE_TTL:
        return _cache["leads"]

    if not DATABASE_URL:
        return []
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(_SQL_RAIO_LEADS)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        leads = [dict(r) for r in rows if _is_raio_lead(dict(r))]
        _cache["leads"] = leads
        _cache["leads_at"] = now
        return leads
    except Exception:
        return _cache["leads"] or []


def get_lead_by_id(lead_id):
    """Busca um lead por id (sem carregar a lista inteira)."""
    if not DATABASE_URL:
        return None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(_SQL_LEAD_BY_ID, (lead_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return None
        lead = dict(row)
        return lead if _is_raio_lead(lead) else lead  # detalhe ainda retorna se existir
    except Exception:
        # fallback cache
        for l in (_cache["leads"] or []):
            if l.get("id") == lead_id:
                return l
        return None


def _invalidate_cache():
    _cache["leads"] = None
    _cache["leads_at"] = 0
    _cache["stats"] = None
    _cache["stats_at"] = 0


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
        return redirect("/")
    error = None
    if request.method == "POST":
        if request.form.get("username") == DASHBOARD_USER and request.form.get("password") == DASHBOARD_PASS:
            session["logged_in"] = True
            return redirect("/")
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
    return jsonify(get_all_leads())


@app.route("/api/leads/<int:lead_id>")
@login_required
def api_lead_detail(lead_id):
    lead = get_lead_by_id(lead_id)
    if not lead:
        return jsonify({"error": "Not found"}), 404
    return jsonify(lead)


@app.route("/api/leads/<int:lead_id>/status", methods=["PUT"])
@login_required
def api_update_status(lead_id):
    data = request.get_json() or {}
    try:
        conn = get_db()
        cur = conn.cursor()
        now = datetime.now().isoformat()
        status = (data.get("status") or "").lower()
        if status:
            if status == "contactado":
                cur.execute(
                    "UPDATE companies SET contacted_at = %s WHERE id = %s",
                    (now, lead_id),
                )
            elif status in ("convertido", "descartado"):
                cur.execute(
                    "UPDATE companies SET contacted_at = COALESCE(contacted_at, %s), notes = %s WHERE id = %s",
                    (now, status.capitalize(), lead_id),
                )
        if "notes" in data and data["notes"] is not None:
            cur.execute("UPDATE companies SET notes = %s WHERE id = %s", (data["notes"], lead_id))
        conn.commit()
        cur.close()
        conn.close()
        _invalidate_cache()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/stats")
@login_required
def api_stats():
    now = time.time()
    if _cache["stats"] is not None and (now - _cache["stats_at"]) < _CACHE_TTL:
        return jsonify(_cache["stats"])

    leads = get_all_leads()
    nichos = {}
    contactados = 0
    descartados = 0
    convertidos = 0
    for l in leads:
        n = l.get("niche") or "outro"
        nichos[n] = nichos.get(n, 0) + 1
        notes = (l.get("notes") or "").lower()
        cs = (l.get("contact_status") or "").lower()
        if notes == "descartado" or cs == "descartado":
            descartados += 1
        elif notes == "convertido" or cs == "convertido":
            convertidos += 1
        if l.get("contacted_at") or cs == "contactado":
            contactados += 1

    payload = {
        "total": len(leads),
        "quentes": len(leads),
        "mornos": contactados,
        "frios": descartados,
        "contactados": contactados,
        "convertidos": convertidos,
        "descartados": descartados,
        "novos": max(len(leads) - contactados, 0),
        "nichos": nichos,
        "top": sorted(leads, key=lambda x: x.get("lead_score") or 0, reverse=True)[:5],
    }
    _cache["stats"] = payload
    _cache["stats_at"] = now
    return jsonify(payload)


@app.route("/api/reports/<period>")
@login_required
def api_reports(period):
    leads = get_all_leads()
    days = 1 if period == "daily" else 7 if period == "weekly" else 30
    cutoff = datetime.now() - timedelta(days=days)
    filtered = []
    for l in leads:
        try:
            created = datetime.fromisoformat(str(l.get("created_at", "")).replace("Z", "").split("+")[0])
            if created >= cutoff:
                filtered.append(l)
        except Exception:
            filtered.append(l)

    problemas = {}
    contactados = 0
    convertidos = 0
    for l in filtered:
        try:
            for p in json.loads(l.get("lead_problems") or "[]"):
                k = p.split(" (")[0]
                problemas[k] = problemas.get(k, 0) + 1
        except Exception:
            pass
        notes = (l.get("notes") or "").lower()
        cs = (l.get("contact_status") or "").lower()
        if l.get("contacted_at") or cs == "contactado":
            contactados += 1
        if notes == "convertido" or cs == "convertido":
            convertidos += 1

    total = len(filtered)
    return jsonify({
        "total": total,
        "quentes": total,
        "mornos": contactados,
        "frios": 0,
        "contactados": contactados,
        "conversao_taxa": round((convertidos / max(contactados, 1)) * 100, 1),
        "problemas_comuns": problemas,
    })


@app.route("/api/export/csv")
@login_required
def api_export_csv():
    leads = get_all_leads(use_cache=False)
    output = io.StringIO()
    output.write("\ufeff")
    w = csv.writer(output, delimiter=";")
    w.writerow(["Nome", "Telefone", "Cidade", "Estado", "Nicho", "Score", "Status", "Problemas"])
    for l in leads:
        notes = (l.get("notes") or "").lower()
        cs = (l.get("contact_status") or "").lower()
        if notes == "descartado" or cs == "descartado":
            st = "descartado"
        elif notes == "convertido" or cs == "convertido":
            st = "convertido"
        elif l.get("contacted_at") or cs == "contactado":
            st = "contactado"
        else:
            st = "novo"
        w.writerow([
            l.get("name", ""),
            l.get("phone", ""),
            l.get("city", ""),
            l.get("state", ""),
            l.get("niche", ""),
            l.get("lead_score", 0),
            st,
            l.get("lead_problems", ""),
        ])
    resp = make_response(output.getvalue())
    resp.headers["Content-Disposition"] = "attachment; filename=leads_raio.csv"
    resp.headers["Content-type"] = "text/csv; charset=utf-8"
    return resp


@app.route("/api/settings")
@login_required
def api_settings():
    return jsonify({"niches": [], "cities": []})


handler = app
