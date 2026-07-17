import sys
import os
import io
import csv
import json
import time
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, jsonify, render_template, request, make_response, session, redirect, url_for

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

app = Flask(__name__, template_folder="../templates")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "prospector_secret")

DATABASE_URL = os.getenv("DATABASE_URL", "")
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "patrao")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASS", "Ronaldete1")

_SOCIAL_MARKERS = (
    "instagram.com", "facebook.com", "fb.com", "linktr.ee",
    "bio.link", "wa.me", "whatsapp.com", "tiktok.com",
)

_LIST_COLS = """
    id, name, phone, city, state, niche, category, address,
    website, website_status, maps_url, rating, review_count,
    instagram_url, instagram_username, lead_score, lead_class,
    lead_problems, lead_services, lead_priority,
    contacted_at, notes, created_at, scraped_at,
    assigned_to, assigned_at
"""

_SQL_RAIO_BASE = f"""
SELECT {_LIST_COLS}
FROM companies
WHERE (
    website_status IN ('sem_site', 'so_social')
    OR website IS NULL
    OR TRIM(COALESCE(website, '')) = ''
    OR lead_class = 'raio'
    OR website ILIKE '%%instagram.com%%'
    OR website ILIKE '%%facebook.com%%'
    OR website ILIKE '%%linktr.ee%%'
)
"""

_cache = {"leads": {}, "leads_at": {}, "stats": {}, "stats_at": {}}
_CACHE_TTL = 45


def get_db():
    return psycopg2.connect(DATABASE_URL)


def _is_raio_lead(lead):
    status = (lead.get("website_status") or "").strip().lower()
    if status in ("sem_site", "so_social"):
        return True
    website = (lead.get("website") or "").strip()
    if not website:
        return True
    lower = website.lower()
    return any(m in lower for m in _SOCIAL_MARKERS)


def _session_user():
    return {
        "id": session.get("user_id"),
        "username": session.get("username") or "",
        "role": session.get("role") or "client",
    }


def _is_admin() -> bool:
    # Só admin de verdade (Patrão) — não promove qualquer login legado
    role = (_session_user().get("role") or "").lower()
    if role == "admin":
        return True
    uname = (_session_user().get("username") or "").lower()
    if uname and uname == (DASHBOARD_USER or "patrao").lower():
        return True
    return False


def get_all_leads(use_cache=True):
    """Leads Raio. Client só vê assigned_to = ele; admin vê todos."""
    uid = _session_user().get("id")
    role = _session_user().get("role") or "admin"
    cache_key = f"{role}:{uid}"

    now = time.time()
    if (
        use_cache
        and cache_key in _cache["leads"]
        and (now - _cache["leads_at"].get(cache_key, 0)) < _CACHE_TTL
    ):
        return _cache["leads"][cache_key]

    if not DATABASE_URL:
        return []
    try:
        # garante schema multi-user
        try:
            from src.users import ensure_schema
            ensure_schema()
        except Exception:
            pass

        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        sql = _SQL_RAIO_BASE
        params = []
        if role != "admin" and uid:
            sql += " AND assigned_to = %s"
            params.append(int(uid))
        sql += " ORDER BY lead_score DESC NULLS LAST;"
        try:
            cur.execute(sql, params)
        except Exception:
            # fallback se colunas assigned_* ainda não existem
            cur.execute(
                """
                SELECT id, name, phone, city, state, niche, category, address,
                       website, website_status, maps_url, rating, review_count,
                       instagram_url, instagram_username, lead_score, lead_class,
                       lead_problems, lead_services, lead_priority,
                       contacted_at, notes, created_at, scraped_at
                FROM companies
                WHERE website_status IN ('sem_site', 'so_social')
                   OR website IS NULL OR TRIM(COALESCE(website, '')) = ''
                   OR lead_class = 'raio'
                ORDER BY lead_score DESC NULLS LAST;
                """
            )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        leads = [dict(r) for r in rows if _is_raio_lead(dict(r))]
        # client sem id não vê nada (força setup de users)
        if role != "admin" and not uid:
            leads = []
        _cache["leads"][cache_key] = leads
        _cache["leads_at"][cache_key] = now
        return leads
    except Exception:
        return _cache["leads"].get(cache_key) or []


def get_lead_by_id(lead_id):
    leads = get_all_leads(use_cache=True)
    lead = next((l for l in leads if l.get("id") == lead_id), None)
    if lead:
        return lead
    # admin pode buscar direto
    if not _is_admin() or not DATABASE_URL:
        return None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"SELECT {_LIST_COLS} FROM companies WHERE id = %s LIMIT 1;", (lead_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def _invalidate_cache():
    _cache["leads"] = {}
    _cache["leads_at"] = {}
    _cache["stats"] = {}
    _cache["stats_at"] = {}


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({"error": "Unauthorized"}), 401
        if not _is_admin():
            return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect("/")
    error = None
    if request.method == "POST":
        username = request.form.get("username") or ""
        password = request.form.get("password") or ""
        user = None
        try:
            from src.users import authenticate
            user = authenticate(username, password)
        except Exception:
            # fallback env = Patrão
            if (username or "").lower() == (DASHBOARD_USER or "").lower() and password == DASHBOARD_PASS:
                user = {
                    "id": 0,
                    "username": (DASHBOARD_USER or "patrao").lower(),
                    "role": "admin",
                    "monthly_quota": 9999,
                    "label": "Patrão",
                }
        if user:
            session["logged_in"] = True
            session["user_id"] = user.get("id")
            session["username"] = user.get("username")
            session["role"] = user.get("role") or "client"
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
@app.route("/users")
@login_required
def dashboard(lead_id=None):
    return render_template("index.html")


@app.route("/api/me")
@login_required
def api_me():
    u = _session_user()
    payload = {
        "id": u.get("id"),
        "username": u.get("username"),
        "role": u.get("role"),
        "is_admin": _is_admin(),
    }
    if u.get("id") and not _is_admin():
        try:
            from src.users import count_assigned_this_month, get_user_by_id
            full = get_user_by_id(int(u["id"])) or {}
            payload["monthly_quota"] = full.get("monthly_quota", 0)
            payload["assigned_this_month"] = count_assigned_this_month(int(u["id"]))
            payload["label"] = full.get("label") or u.get("username")
        except Exception:
            pass
    return jsonify(payload)


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
    # client só altera lead dele
    lead = get_lead_by_id(lead_id)
    if not lead:
        return jsonify({"error": "Not found"}), 404
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
        # admin: reatribuir
        if _is_admin() and "assigned_to" in data:
            from src.users import manual_assign
            aid = data.get("assigned_to")
            manual_assign(lead_id, int(aid) if aid else None)
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
    uid = _session_user().get("id")
    role = _session_user().get("role")
    cache_key = f"{role}:{uid}"
    now = time.time()
    if cache_key in _cache["stats"] and (now - _cache["stats_at"].get(cache_key, 0)) < _CACHE_TTL:
        return jsonify(_cache["stats"][cache_key])

    leads = get_all_leads()
    nichos = {}
    contactados = 0
    descartados = 0
    convertidos = 0
    for l in leads:
        n = l.get("niche") or "outro"
        nichos[n] = nichos.get(n, 0) + 1
        notes = (l.get("notes") or "").lower()
        if notes == "descartado":
            descartados += 1
        elif notes == "convertido":
            convertidos += 1
        if l.get("contacted_at"):
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
    _cache["stats"][cache_key] = payload
    _cache["stats_at"][cache_key] = now
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
        if l.get("contacted_at"):
            contactados += 1
        if notes == "convertido":
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
    headers = ["Nome", "Telefone", "Cidade", "Estado", "Nicho", "Score", "Status", "Problemas"]
    if _is_admin():
        headers.append("AssignedTo")
    w.writerow(headers)
    for l in leads:
        notes = (l.get("notes") or "").lower()
        if notes == "descartado":
            st = "descartado"
        elif notes == "convertido":
            st = "convertido"
        elif l.get("contacted_at"):
            st = "contactado"
        else:
            st = "novo"
        row = [
            l.get("name", ""),
            l.get("phone", ""),
            l.get("city", ""),
            l.get("state", ""),
            l.get("niche", ""),
            l.get("lead_score", 0),
            st,
            l.get("lead_problems", ""),
        ]
        if _is_admin():
            row.append(l.get("assigned_to") or "")
        w.writerow(row)
    resp = make_response(output.getvalue())
    resp.headers["Content-Disposition"] = "attachment; filename=leads_raio.csv"
    resp.headers["Content-type"] = "text/csv; charset=utf-8"
    return resp


@app.route("/api/users", methods=["GET"])
@login_required
@admin_required
def api_users_list():
    from src.users import list_users, ensure_schema
    ensure_schema()
    return jsonify(list_users())


@app.route("/api/users", methods=["POST"])
@login_required
@admin_required
def api_users_create():
    data = request.get_json(silent=True) or {}
    try:
        from src.users import create_user, ensure_schema
        ensure_schema()
        user = create_user(
            username=data.get("username", ""),
            password=data.get("password", ""),
            monthly_quota=int(data.get("monthly_quota") or 50),
            role=data.get("role") or "client",
            cities=data.get("cities") or [],
            niches=data.get("niches") or [],
            label=data.get("label") or "",
        )
        _invalidate_cache()
        return jsonify(user), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/users/<int:user_id>", methods=["PUT"])
@login_required
@admin_required
def api_users_update(user_id):
    from src.users import update_user
    data = request.get_json() or {}
    try:
        user = update_user(user_id, **data)
        _invalidate_cache()
        return jsonify(user or {})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/leads/<int:lead_id>/assign", methods=["PUT"])
@login_required
@admin_required
def api_lead_assign(lead_id):
    from src.users import manual_assign
    data = request.get_json() or {}
    aid = data.get("assigned_to")
    ok = manual_assign(lead_id, int(aid) if aid not in (None, "", 0, "0") else None)
    _invalidate_cache()
    return jsonify({"success": ok})


@app.route("/api/settings")
@login_required
def api_settings():
    return jsonify({"niches": [], "cities": []})


handler = app
