import sys
import os
import io
import csv
import json
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

def get_db():
    return psycopg2.connect(DATABASE_URL)

def get_all_leads():
    if not DATABASE_URL:
        return []
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM companies ORDER BY lead_score DESC;")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]
    except:
        return []

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
    leads = get_all_leads()
    lead = next((l for l in leads if l["id"] == lead_id), None)
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

@app.route("/api/stats")
@login_required
def api_stats():
    leads = get_all_leads()
    nichos = {}
    for l in leads:
        n = l.get("niche") or "outro"
        nichos[n] = nichos.get(n, 0) + 1
    return jsonify({
        "total": len(leads),
        "quentes": len([l for l in leads if l.get("lead_class") == "quente"]),
        "mornos": len([l for l in leads if l.get("lead_class") == "morno"]),
        "frios": len([l for l in leads if l.get("lead_class") == "frio"]),
        "descartados": len([l for l in leads if l.get("notes") == "Descartado"]),
        "nichos": nichos
    })

@app.route("/api/reports/<period>")
@login_required
def api_reports(period):
    leads = get_all_leads()
    days = 1 if period == "daily" else 7 if period == "weekly" else 30
    cutoff = datetime.now() - timedelta(days=days)
    filtered = []
    for l in leads:
        try:
            created = datetime.fromisoformat(l.get("created_at", "").replace("Z", "").split("+")[0])
            if created >= cutoff:
                filtered.append(l)
        except:
            filtered.append(l)
    problemas = {}
    for l in filtered:
        try:
            for p in json.loads(l.get("lead_problems") or "[]"):
                k = p.split(" (")[0]
                problemas[k] = problemas.get(k, 0) + 1
        except:
            pass
    total = len(filtered)
    contactados = len([l for l in filtered if l.get("contacted_at")])
    convertidos = len([l for l in filtered if l.get("notes") == "Convertido"])
    return jsonify({
        "total": total,
        "quentes": len([l for l in filtered if l.get("lead_class") == "quente"]),
        "mornos": len([l for l in filtered if l.get("lead_class") == "morno"]),
        "frios": len([l for l in filtered if l.get("lead_class") == "frio"]),
        "conversao_taxa": round((convertidos / max(contactados, 1)) * 100, 1),
        "problemas_comuns": problemas
    })

@app.route("/api/export/csv")
@login_required
def api_export_csv():
    leads = get_all_leads()
    output = io.StringIO()
    output.write("\ufeff")
    w = csv.writer(output, delimiter=";")
    w.writerow(["Nome","Telefone","Cidade","Nicho","Score","Classe","Website","Instagram","Problemas"])
    for l in leads:
        w.writerow([l.get("name",""),l.get("phone",""),l.get("city",""),l.get("niche",""),l.get("lead_score",0),l.get("lead_class",""),l.get("website",""),l.get("instagram_url",""),l.get("lead_problems","")])
    resp = make_response(output.getvalue())
    resp.headers["Content-Disposition"] = f"attachment; filename=leads.csv"
    resp.headers["Content-type"] = "text/csv; charset=utf-8"
    return resp

@app.route("/api/settings")
@login_required
def api_settings():
    return jsonify({"niches": [], "cities": []})

# Isso é pro Vercel reconhecer
handler = app