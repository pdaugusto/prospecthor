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
# Patrão fixo — não usar "admin" (conta do amigo)
DASHBOARD_USER = "patrao"
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
    """Usuário efetivo (pode ser o cliente em modo impersonate)."""
    return {
        "id": session.get("user_id"),
        "username": session.get("username") or "",
        "role": session.get("role") or "client",
        "label": session.get("label") or "",
    }


def _real_session_user():
    """Quem realmente logou (Patrão), mesmo em impersonate."""
    if session.get("impersonating"):
        return {
            "id": session.get("real_user_id"),
            "username": session.get("real_username") or "patrao",
            "role": session.get("real_role") or "admin",
        }
    return _session_user()


def _is_impersonating() -> bool:
    return bool(session.get("impersonating"))


def _is_real_admin() -> bool:
    """Só o Patrão real (login patrao), nunca o cliente impersonado."""
    ru = _real_session_user()
    uname = (ru.get("username") or "").lower().strip()
    if uname in ("admin", "teste_amigo"):
        return False
    if uname in ("patrao", "patrão"):
        return True
    return (ru.get("role") or "").lower() == "admin" and uname == "patrao"


def _is_admin() -> bool:
    """
    Poder de admin no painel (vê TODOS os leads, Usuários, etc.).
    Em impersonate = False.
    Nunca confiar só em role=admin se username não for patrao.
    """
    if _is_impersonating():
        return False
    uname = (_session_user().get("username") or "").lower().strip()
    if uname != "patrao" and uname != "patrão":
        return False
    return _is_real_admin()


def _audit_actor() -> tuple[int | None, str]:
    """Username gravado no log: 'patrao (impersonate:admin)' se aplicável."""
    if _is_impersonating():
        real = _real_session_user()
        eff = _session_user()
        uname = f"{real.get('username') or 'patrao'} (impersonate:{eff.get('username') or '?'})"
        return real.get("id"), uname
    u = _session_user()
    return u.get("id"), (u.get("username") or "sistema")


def _effective_user_id() -> int | None:
    """ID do usuário efetivo na sessão (cliente ou impersonado)."""
    uid = _session_user().get("id")
    try:
        if uid in (None, "", 0, "0"):
            return None
        return int(uid)
    except (TypeError, ValueError):
        return None


def _is_patrao_view() -> bool:
    """Patrão real, fora de impersonate — pode ver pool e filtrar."""
    return _is_admin() and not _is_impersonating()


def _sees_all_leads() -> bool:
    """Compat: poder de acessar qualquer lead (detalhe/assign)."""
    return _is_patrao_view()


def _parse_leads_scope() -> tuple[str, int | None]:
    """
    Escopo da lista (só Patrão usa):
      free  → sobras (assigned_to IS NULL)  [DEFAULT]
      all   → todos
      user  → assigned_to = user_id
    Cliente ignora e sempre vê só os dele.
    """
    scope = (request.args.get("scope") or "free").strip().lower()
    if scope not in ("free", "all", "user"):
        scope = "free"
    owner_id = None
    raw = request.args.get("user_id")
    if raw not in (None, "", "null"):
        try:
            owner_id = int(raw)
        except (TypeError, ValueError):
            owner_id = None
    if scope == "user" and not owner_id:
        scope = "free"
    return scope, owner_id


def _apply_patrao_scope(leads: list, scope: str, owner_id: int | None) -> list:
    """Filtra lista do Patrão por escopo (default = só sobras)."""
    if scope == "all":
        return list(leads or [])
    if scope == "user" and owner_id:
        out = []
        for l in leads or []:
            try:
                if l.get("assigned_to") is not None and int(l.get("assigned_to")) == int(owner_id):
                    out.append(l)
            except (TypeError, ValueError):
                continue
        return out
    # free / default: só sobras
    return [l for l in (leads or []) if l.get("assigned_to") is None]


def _filter_leads_for_session(leads: list, scope: str = "free", owner_id: int | None = None) -> list:
    """
    Isolamento final:
    - Cliente / impersonate → só assigned_to == user_id
    - Patrão → aplica scope (default free = sobras para encaminhar)
    """
    if _is_patrao_view():
        return _apply_patrao_scope(leads, scope, owner_id)
    uid = _effective_user_id()
    if not uid:
        return []
    out = []
    for l in leads or []:
        try:
            owner = l.get("assigned_to")
            if owner is not None and int(owner) == uid:
                out.append(l)
        except (TypeError, ValueError):
            continue
    return out


def get_all_leads(use_cache=True, scope: str | None = None, owner_id: int | None = None):
    """
    Leads Raio:
    - Patrão: por padrão SÓ sobras (assigned_to NULL); scope=all|user sob demanda
    - Cliente: SOMENTE assigned_to == user_id
    """
    uid_int = _effective_user_id()
    uname = (_session_user().get("username") or "").lower().strip()
    is_patrao = _is_patrao_view()

    if is_patrao:
        if scope is None:
            scope, owner_id = _parse_leads_scope()
        scope = (scope or "free").lower()
        if scope not in ("free", "all", "user"):
            scope = "free"
    else:
        scope = "own"
        owner_id = uid_int

    cache_key = f"{scope}:{owner_id}:{uid_int}:{uname}"

    now = time.time()
    if (
        use_cache
        and cache_key in _cache["leads"]
        and (now - _cache["leads_at"].get(cache_key, 0)) < _CACHE_TTL
    ):
        return _filter_leads_for_session(_cache["leads"][cache_key], scope, owner_id)

    if not DATABASE_URL:
        return []

    # Cliente sem id válido = lista vazia
    if not is_patrao and not uid_int:
        return []

    try:
        try:
            from src.users import ensure_schema
            ensure_schema()
        except Exception:
            pass

        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        if is_patrao:
            if scope == "all":
                sql = _SQL_RAIO_BASE + " ORDER BY lead_score DESC NULLS LAST;"
                cur.execute(sql)
            elif scope == "user" and owner_id:
                sql = (
                    _SQL_RAIO_BASE
                    + " AND assigned_to = %s ORDER BY lead_score DESC NULLS LAST;"
                )
                cur.execute(sql, (int(owner_id),))
            else:
                # default: sobras
                sql = (
                    _SQL_RAIO_BASE
                    + " AND assigned_to IS NULL ORDER BY lead_score DESC NULLS LAST;"
                )
                cur.execute(sql)
        else:
            sql = (
                _SQL_RAIO_BASE
                + " AND assigned_to = %s ORDER BY lead_score DESC NULLS LAST;"
            )
            cur.execute(sql, (uid_int,))

        rows = cur.fetchall()
        cur.close()
        conn.close()
        leads = [dict(r) for r in rows if _is_raio_lead(dict(r))]
        leads = _filter_leads_for_session(leads, scope, owner_id)

        _cache["leads"][cache_key] = leads
        _cache["leads_at"][cache_key] = now
        return leads
    except Exception:
        if not is_patrao:
            return []
        return _filter_leads_for_session(_cache["leads"].get(cache_key) or [], scope, owner_id)


def _pool_summary() -> dict:
    """Contadores do pool (Patrão): livres / atribuídos / total + uso por cliente."""
    empty = {"free": 0, "assigned": 0, "all": 0, "users": []}
    if not DATABASE_URL or not _is_patrao_view():
        return empty
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            f"""
            SELECT
              COUNT(*) FILTER (WHERE assigned_to IS NULL) AS free,
              COUNT(*) FILTER (WHERE assigned_to IS NOT NULL) AS assigned,
              COUNT(*) AS total
            FROM companies
            WHERE (
                website_status IN ('sem_site', 'so_social')
                OR website IS NULL
                OR TRIM(COALESCE(website, '')) = ''
                OR lead_class = 'raio'
                OR website ILIKE '%%instagram.com%%'
                OR website ILIKE '%%facebook.com%%'
                OR website ILIKE '%%linktr.ee%%'
            );
            """
        )
        row = cur.fetchone() or {}
        free = int(row.get("free") or 0)
        assigned = int(row.get("assigned") or 0)
        total = int(row.get("total") or 0)

        from src.users import list_users, count_assigned_this_month
        users_out = []
        for u in list_users():
            if (u.get("username") or "").lower() == "patrao":
                continue
            uid = int(u["id"])
            used = count_assigned_this_month(uid)
            quota = int(u.get("monthly_quota") or 0)
            users_out.append({
                "id": uid,
                "username": u.get("username"),
                "label": u.get("label") or u.get("username"),
                "active": u.get("active"),
                "used": used,
                "quota": quota,
                "full": quota > 0 and used >= quota,
            })
        cur.close()
        conn.close()
        return {"free": free, "assigned": assigned, "all": total, "users": users_out}
    except Exception:
        return empty


@app.after_request
def _no_store_private(resp):
    """Evita CDN/browser reutilizar /api/leads de outro usuário."""
    path = (request.path or "")
    if path.startswith("/api/"):
        resp.headers["Cache-Control"] = "private, no-store, no-cache, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Vary"] = "Cookie"
    return resp


def get_lead_by_id(lead_id):
    # tenta na lista do escopo atual (sobras etc.)
    if _is_patrao_view():
        # Patrão: busca direta no banco (pode abrir lead atribuído mesmo com lista = sobras)
        if not DATABASE_URL:
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

    leads = get_all_leads(use_cache=True, scope="own", owner_id=_effective_user_id())
    lead = next((l for l in leads if l.get("id") == lead_id), None)
    return lead


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
    """Rotas só do Patrão real (bloqueia durante impersonate)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({"error": "Unauthorized"}), 401
        if not _is_real_admin():
            return jsonify({"error": "Forbidden"}), 403
        if _is_impersonating() and request.path.startswith("/api/"):
            # impersonate: só bloqueia rotas admin (users/audit/bot)
            blocked = (
                request.path.startswith("/api/users")
                or request.path.startswith("/api/audit")
                or request.path.startswith("/api/bot-status")
                or request.path.startswith("/api/impersonate")
                and request.path.rstrip("/").endswith("/impersonate")
            )
            # allow stop impersonate
            if request.path.rstrip("/").endswith("/impersonate/stop"):
                return f(*args, **kwargs)
            if (
                request.path.startswith("/api/users")
                or request.path.startswith("/api/audit")
                or request.path.startswith("/api/bot-status")
            ):
                return jsonify({"error": "Indisponível em modo impersonate. Volte à sua conta."}), 403
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
            session.clear()
            session["logged_in"] = True
            # id obrigatório para cliente filtrar leads (sem id = lista vazia)
            session["user_id"] = user.get("id")
            session["username"] = (user.get("username") or "").strip()
            # nunca promover client a admin na sessão
            role = (user.get("role") or "client").lower()
            uname = session["username"].lower()
            if uname == "patrao":
                role = "admin"
            elif role == "admin" and uname != "patrao":
                role = "client"
            session["role"] = role
            session["label"] = user.get("label") or user.get("username")
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
@app.route("/audit")
@app.route("/bot")
@login_required
def dashboard(lead_id=None):
    return render_template("index.html")


@app.route("/api/me")
@login_required
def api_me():
    u = _session_user()
    real = _real_session_user()
    imp = _is_impersonating()
    is_adm = _is_admin()  # False se impersonate
    is_real_adm = _is_real_admin()

    payload = {
        "id": u.get("id"),
        "username": u.get("username"),
        "role": "admin" if is_adm else (u.get("role") or "client"),
        "is_admin": is_adm,
        "is_real_admin": is_real_adm,
        "impersonating": imp,
        "label": u.get("label") or u.get("username"),
    }
    if imp:
        payload["real_username"] = real.get("username")
        payload["real_user_id"] = real.get("id")
        payload["impersonate_label"] = (
            session.get("impersonate_label")
            or u.get("label")
            or u.get("username")
        )
        # em impersonate: menu admin some, mas banner usa is_real_admin
        payload["is_admin"] = False
        payload["role"] = "client"

    if u.get("id") and (not is_adm or imp):
        try:
            from src.users import count_assigned_this_month, get_user_by_id
            full = get_user_by_id(int(u["id"])) or {}
            payload["monthly_quota"] = full.get("monthly_quota", 0)
            payload["assigned_this_month"] = count_assigned_this_month(int(u["id"]))
            payload["label"] = full.get("label") or u.get("username")
            if imp:
                payload["impersonate_label"] = payload["label"]
        except Exception:
            pass
    elif is_adm and not imp:
        payload["label"] = "Patrão"
    return jsonify(payload)


@app.route("/api/impersonate", methods=["POST"])
@login_required
def api_impersonate_start():
    """Patrão assume visão de um cliente (não de outro admin)."""
    if not _is_real_admin() or _is_impersonating():
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json(silent=True) or {}
    target_id = data.get("user_id")
    if not target_id:
        return jsonify({"error": "user_id obrigatório"}), 400
    try:
        from src.users import get_user_by_id
        from src.audit import log_action
        target = get_user_by_id(int(target_id))
        if not target:
            return jsonify({"error": "Usuário não encontrado"}), 404
        t_user = (target.get("username") or "").lower()
        t_role = (target.get("role") or "").lower()
        if t_user == "patrao" or t_role == "admin":
            return jsonify({"error": "Não é permitido impersonate de admin/Patrão"}), 400
        # permite ver mesmo se ATIVO off (só não recebe leads do bot)

        # guarda sessão real
        session["impersonating"] = True
        session["real_user_id"] = session.get("user_id")
        session["real_username"] = session.get("username")
        session["real_role"] = session.get("role") or "admin"
        # assume cliente
        session["user_id"] = target.get("id")
        session["username"] = target.get("username")
        session["role"] = "client"
        session["label"] = target.get("label") or target.get("username")
        session["impersonate_label"] = session["label"]

        log_action(
            "impersonate_start",
            user_id=session.get("real_user_id"),
            username=f"{session.get('real_username')} (impersonate:{target.get('username')})",
            details={"target_id": target.get("id"), "target": target.get("username")},
        )
        _invalidate_cache()
        return jsonify({
            "success": True,
            "impersonating": True,
            "username": target.get("username"),
            "label": session["label"],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/impersonate/stop", methods=["POST"])
@login_required
def api_impersonate_stop():
    """Volta à conta do Patrão."""
    if not _is_impersonating():
        return jsonify({"success": True, "impersonating": False})
    if not _is_real_admin():
        return jsonify({"error": "Forbidden"}), 403
    try:
        from src.audit import log_action
        target = session.get("username")
        real_id = session.get("real_user_id")
        real_user = session.get("real_username") or "patrao"
        real_role = session.get("real_role") or "admin"

        log_action(
            "impersonate_stop",
            user_id=real_id,
            username=f"{real_user} (impersonate:{target})",
            details={"was": target},
        )

        session["user_id"] = real_id
        session["username"] = real_user
        session["role"] = real_role
        session["label"] = "Patrão"
        session.pop("impersonating", None)
        session.pop("real_user_id", None)
        session.pop("real_username", None)
        session.pop("real_role", None)
        session.pop("impersonate_label", None)
        _invalidate_cache()
        return jsonify({"success": True, "impersonating": False, "username": real_user})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/leads")
@login_required
def api_leads():
    # Patrão: scope=free (default) | all | user&user_id=
    # Cliente: só os dele
    if _is_patrao_view():
        scope, owner_id = _parse_leads_scope()
        return jsonify(get_all_leads(use_cache=True, scope=scope, owner_id=owner_id))
    return jsonify(get_all_leads(use_cache=True, scope="own", owner_id=_effective_user_id()))


@app.route("/api/leads/<int:lead_id>")
@login_required
def api_lead_detail(lead_id):
    lead = get_lead_by_id(lead_id)
    if not lead:
        return jsonify({"error": "Not found"}), 404
    # cliente nunca acessa lead de outro (livre ou de outrem)
    if not _is_patrao_view():
        uid = _effective_user_id()
        try:
            owner = lead.get("assigned_to")
            if owner is None or int(owner) != uid:
                return jsonify({"error": "Not found"}), 404
        except (TypeError, ValueError):
            return jsonify({"error": "Not found"}), 404
    return jsonify(lead)


@app.route("/api/leads/<int:lead_id>/status", methods=["PUT", "POST"])
@login_required
def api_update_status(lead_id):
    # client só altera lead dele
    lead = get_lead_by_id(lead_id)
    if not lead:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    try:
        from src.audit import log_action
        audit_uid, audit_uname = _audit_actor()
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
            log_action(
                f"status_{status}",
                user_id=audit_uid,
                username=audit_uname,
                lead_id=lead_id,
                company_name=lead.get("name"),
                details={"status": status, "impersonating": _is_impersonating()},
            )
        if "notes" in data and data["notes"] is not None:
            cur.execute("UPDATE companies SET notes = %s WHERE id = %s", (data["notes"], lead_id))
            log_action(
                "nota",
                user_id=audit_uid,
                username=audit_uname,
                lead_id=lead_id,
                company_name=lead.get("name"),
                details={"notes_preview": str(data["notes"])[:200], "impersonating": _is_impersonating()},
            )
        # admin real (não em impersonate): reatribuir
        if _is_admin() and "assigned_to" in data:
            from src.users import manual_assign
            aid = data.get("assigned_to")
            manual_assign(lead_id, int(aid) if aid not in (None, "", 0, "0") else None)
            log_action(
                "assign",
                user_id=audit_uid,
                username=audit_uname,
                lead_id=lead_id,
                company_name=lead.get("name"),
                details={"assigned_to": aid},
            )
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
    uid = _effective_user_id()
    uname = (_session_user().get("username") or "").lower().strip()
    if _is_patrao_view():
        scope, owner_id = _parse_leads_scope()
    else:
        scope, owner_id = "own", uid
    cache_key = f"stats:{scope}:{owner_id}:{uid}:{uname}"
    now = time.time()
    if cache_key in _cache["stats"] and (now - _cache["stats_at"].get(cache_key, 0)) < _CACHE_TTL:
        return jsonify(_cache["stats"][cache_key])

    if _is_patrao_view():
        leads = get_all_leads(use_cache=True, scope=scope, owner_id=owner_id)
    else:
        leads = get_all_leads(use_cache=True, scope="own", owner_id=uid)

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
        "scope": scope,
    }
    if _is_patrao_view():
        pool = _pool_summary()
        payload["pool"] = pool
        # atalhos no topo
        payload["free"] = pool.get("free", 0)
        payload["assigned_total"] = pool.get("assigned", 0)
        payload["all_total"] = pool.get("all", 0)
    _cache["stats"][cache_key] = payload
    _cache["stats_at"][cache_key] = now
    return jsonify(payload)


@app.route("/api/reports/<period>")
@login_required
def api_reports(period):
    # Patrão: relatório no escopo atual (default sobras); cliente: só os dele
    if _is_patrao_view():
        scope, owner_id = _parse_leads_scope()
        leads = get_all_leads(use_cache=True, scope=scope, owner_id=owner_id)
    else:
        leads = get_all_leads(use_cache=True, scope="own", owner_id=_effective_user_id())
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
    if _is_patrao_view():
        scope, owner_id = _parse_leads_scope()
        leads = get_all_leads(use_cache=False, scope=scope, owner_id=owner_id)
    else:
        leads = get_all_leads(use_cache=False, scope="own", owner_id=_effective_user_id())
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
        from src.audit import log_action
        user = create_user(
            username=data.get("username", ""),
            password=data.get("password", ""),
            monthly_quota=int(data.get("monthly_quota") or 50),
            role=data.get("role") or "client",
            cities=data.get("cities") or [],
            niches=data.get("niches") or [],
            label=data.get("label") or "",
        )
        su = _session_user()
        log_action(
            "user_create",
            user_id=su.get("id"),
            username=su.get("username"),
            details={"created": user.get("username"), "quota": user.get("monthly_quota")},
        )
        _invalidate_cache()
        return jsonify(user), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/users/<int:user_id>", methods=["PUT", "POST"])
@login_required
@admin_required
def api_users_update(user_id):
    """
    Atualiza usuário.
    Aceita PUT e POST (Vercel às vezes só repassa POST de forma confiável).
    POST com {"action":"delete"} remove; {"action":"reset-month"} zera uso do mês.
    """
    from src.users import update_user, delete_user, reset_month_usage, get_user_by_id
    data = request.get_json(silent=True) or {}
    action = (data.get("action") or "").lower().strip()

    try:
        from src.audit import log_action
        su = _session_user()

        if action == "delete" or (request.method == "POST" and data.get("_method") == "DELETE"):
            u0 = get_user_by_id(int(user_id))
            ok = delete_user(int(user_id), reassign_leads_to=None)
            log_action(
                "user_delete",
                user_id=su.get("id"),
                username=su.get("username"),
                details={"deleted": (u0 or {}).get("username"), "id": user_id},
            )
            _invalidate_cache()
            return jsonify({"success": ok})

        if action == "reset-month":
            n = reset_month_usage(int(user_id))
            u = get_user_by_id(int(user_id))
            log_action(
                "user_reset_month",
                user_id=su.get("id"),
                username=su.get("username"),
                details={"target": (u or {}).get("username"), "rows": n},
            )
            _invalidate_cache()
            return jsonify({"success": True, "reset_rows": n, "user": u})

        # update normal — só campos enviados
        payload = {k: v for k, v in data.items() if k not in ("action", "_method")}
        before = get_user_by_id(int(user_id))
        user = update_user(user_id, **payload)
        if not user:
            return jsonify({"error": "Usuário não encontrado"}), 404
        log_action(
            "user_update",
            user_id=su.get("id"),
            username=su.get("username"),
            details={
                "target": user.get("username"),
                "changes": {k: payload.get(k) for k in payload if k != "password"},
                "before_active": (before or {}).get("active"),
                "after_active": user.get("active"),
            },
        )
        _invalidate_cache()
        return jsonify({"success": True, "user": user})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/users/<int:user_id>/reset-month", methods=["POST"])
@login_required
@admin_required
def api_users_reset_month(user_id):
    """Zera contagem de leads recebidos no mês (cota volta a contar do zero)."""
    from src.users import reset_month_usage, get_user_by_id
    try:
        n = reset_month_usage(int(user_id))
        _invalidate_cache()
        u = get_user_by_id(int(user_id))
        return jsonify({"success": True, "reset_rows": n, "user": u})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/users/<int:user_id>", methods=["DELETE"])
@login_required
@admin_required
def api_users_delete(user_id):
    """Remove usuário; leads dele ficam livres (Patrão continua vendo)."""
    from src.users import delete_user
    try:
        ok = delete_user(int(user_id), reassign_leads_to=None)
        _invalidate_cache()
        return jsonify({"success": ok})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/leads/<int:lead_id>/assign", methods=["PUT", "POST"])
@login_required
@admin_required
def api_lead_assign(lead_id):
    """Patrão define dono do lead: assigned_to = user_id ou null (livre)."""
    from src.users import manual_assign
    from src.audit import log_action
    data = request.get_json(silent=True) or {}
    aid = data.get("assigned_to")
    lead = get_lead_by_id(lead_id) or {}
    if aid in (None, "", 0, "0", "null"):
        ok = manual_assign(lead_id, None)
        assigned = None
    else:
        ok = manual_assign(lead_id, int(aid))
        assigned = int(aid)
    audit_uid, audit_uname = _audit_actor()
    log_action(
        "assign",
        user_id=audit_uid,
        username=audit_uname,
        lead_id=lead_id,
        company_name=lead.get("name"),
        details={"assigned_to": assigned},
    )
    _invalidate_cache()
    return jsonify({"success": ok, "assigned_to": assigned})


@app.route("/api/audit")
@login_required
@admin_required
def api_audit():
    from src.audit import query_logs, ensure_schema
    ensure_schema()
    logs = query_logs(
        username=request.args.get("username") or None,
        lead_id=int(request.args["lead_id"]) if request.args.get("lead_id") else None,
        action=request.args.get("action") or None,
        since=request.args.get("since") or None,
        until=request.args.get("until") or None,
        limit=int(request.args.get("limit") or 150),
    )
    return jsonify(logs)


@app.route("/api/bot-status")
@login_required
@admin_required
def api_bot_status():
    from src.bot_status import get_status, ensure_schema
    ensure_schema()
    return jsonify(get_status(log_limit=int(request.args.get("limit") or 15)))


@app.route("/api/settings")
@login_required
def api_settings():
    return jsonify({"niches": [], "cities": []})


handler = app
