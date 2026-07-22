import sys
import os
import io
import csv
import json
import time
import secrets
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from functools import wraps
from collections import defaultdict
from flask import Flask, jsonify, render_template, request, make_response, session, redirect, url_for

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

app = Flask(
    __name__,
    template_folder=os.path.join(_ROOT, "templates"),
    static_folder=os.path.join(_ROOT, "static"),
    static_url_path="/static",
)

import hashlib as _hashlib

DATABASE_URL = os.getenv("DATABASE_URL", "")
# Patrão fixo — nunca default de senha no código
DASHBOARD_USER = "patrao"
DASHBOARD_PASS = (os.getenv("DASHBOARD_PASS") or "").strip()

# Sessão: SEMPRE estável entre instâncias Vercel.
# Se FLASK_SECRET_KEY faltar, deriva do DATABASE_URL (mesma em todos os pods).
# secrets.token_hex aleatório quebrava login/admin entre cold starts.
_secret = (
    os.getenv("FLASK_SECRET_KEY")
    or os.getenv("DASHBOARD_SECRET_KEY")
    or ""
).strip()
if not _secret:
    _seed = DATABASE_URL or os.getenv("VERCEL_GIT_COMMIT_SHA") or "prospecthor-local"
    _secret = _hashlib.sha256(f"prospecthor-session-v1|{_seed}".encode("utf-8")).hexdigest()
app.secret_key = _secret
_is_vercel = bool(os.getenv("VERCEL") or os.getenv("VERCEL_ENV"))
# Sessão longa: fica logado até clicar em Sair (não só 12h)
_SESSION_DAYS = int(os.getenv("SESSION_DAYS") or "90")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Secure só em HTTPS (Vercel). Em HTTP local o cookie precisa poder ser setado.
    SESSION_COOKIE_SECURE=_is_vercel or os.getenv("SESSION_COOKIE_SECURE", "").lower() in ("1", "true"),
    PERMANENT_SESSION_LIFETIME=timedelta(days=max(1, _SESSION_DAYS)),
    # a cada request renova o prazo do cookie
    SESSION_REFRESH_EACH_REQUEST=True,
    SESSION_COOKIE_NAME="prospecthor_session",
    # path / = cookie vale em todas as rotas
    SESSION_COOKIE_PATH="/",
)

# Rate limit simples de login (por IP) — protege brute force em serverless (best-effort)
_login_attempts: dict[str, list[float]] = defaultdict(list)
_LOGIN_MAX = 12
_LOGIN_WINDOW_S = 600  # 10 min

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


def _norm_username(u: str | None) -> str:
    """Normaliza username (patrao / patrão → patrao)."""
    s = (u or "").lower().strip()
    # remove acentos comuns
    for a, b in (("ã", "a"), ("á", "a"), ("â", "a"), ("à", "a"), ("é", "e"), ("ê", "e"), ("ó", "o"), ("ô", "o")):
        s = s.replace(a, b)
    return s


def _is_principal_username(u: str | None) -> bool:
    return _norm_username(u) == "patrao"


def _is_real_admin() -> bool:
    """Só o Patrão real (login patrao), nunca o cliente impersonado."""
    ru = _real_session_user()
    uname = _norm_username(ru.get("username"))
    if uname in ("admin", "teste_amigo"):
        return False
    if uname == "patrao":
        return True
    # role na sessão (login grava admin só pro patrao)
    if (ru.get("role") or "").lower() == "admin" and uname == "patrao":
        return True
    return False


def _is_admin() -> bool:
    """
    Poder de admin no painel (vê TODOS os leads, Usuários, etc.).
    Em impersonate = False.
    Nunca confiar só em role=admin se username não for patrao.
    """
    if _is_impersonating():
        return False
    uname = _norm_username(_session_user().get("username"))
    if uname != "patrao":
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
    - Cliente / impersonate → só assigned_to == user_id E com tel OU Instagram
    - Patrão → aplica scope (default free = sobras para encaminhar)
    """
    if _is_patrao_view():
        return _apply_patrao_scope(leads, scope, owner_id)
    uid = _effective_user_id()
    if not uid:
        return []
    try:
        from src.users import lead_has_client_contact
    except Exception:
        def lead_has_client_contact(c):  # type: ignore
            return bool((c or {}).get("phone"))
    out = []
    for l in leads or []:
        try:
            owner = l.get("assigned_to")
            if owner is not None and int(owner) == uid and lead_has_client_contact(l):
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


@app.before_request
def _keep_session_alive():
    """Enquanto estiver logado, mantém cookie permanente (até Sair)."""
    if session.get("logged_in"):
        session.permanent = True


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login"))
        # renova em toda página autenticada
        session.permanent = True
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
            # allow stop impersonate
            if request.path.rstrip("/").endswith("/impersonate/stop"):
                return f(*args, **kwargs)
            if (
                request.path.startswith("/api/users")
                or request.path.startswith("/api/audit")
                or request.path.startswith("/api/bot-status")
                or request.path.startswith("/api/bot-plan")
            ):
                return jsonify({"error": "Indisponível em modo impersonate. Volte à sua conta."}), 403
        return f(*args, **kwargs)
    return decorated


def _client_ip() -> str:
    # Vercel / proxies
    xff = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return xff or (request.remote_addr or "unknown")


def _login_rate_limited(ip: str) -> bool:
    now = time.time()
    window = _login_attempts[ip]
    # limpa antigos
    _login_attempts[ip] = [t for t in window if now - t < _LOGIN_WINDOW_S]
    return len(_login_attempts[ip]) >= _LOGIN_MAX


def _login_register_fail(ip: str) -> None:
    _login_attempts[ip].append(time.time())


def _packages_for_view():
    """Pacotes com preço formatado + % de economia vs pacote base (Faísca)."""
    try:
        from src.trovoeda import list_packages, ensure_schema
        ensure_schema()
        pkgs = list_packages(active_only=True)
    except Exception:
        pkgs = []
    if not pkgs:
        return []

    # Base = menor sort_order (Faísca) — desconto = economia no R$/lead vs base
    base = pkgs[0]
    base_coins = int(base.get("coins") or 0)
    base_cents = int(base.get("price_cents") or 0)
    base_per = (base_cents / base_coins) if base_coins else 0.0

    out = []
    for p in pkgs:
        coins = int(p.get("coins") or 0)
        cents = int(p.get("price_cents") or 0)
        reais = cents / 100.0
        per = (reais / coins) if coins else 0.0
        per_cents = (cents / coins) if coins else 0.0
        item = dict(p)
        item["price_brl"] = f"{reais:.2f}".replace(".", ",")
        item["per_lead"] = f"{per:.2f}".replace(".", ",") if coins else ""
        save_pct = 0
        if base_per > 0 and per_cents > 0 and coins != base_coins:
            save_pct = max(0, int(round((1.0 - (per_cents / base_per)) * 100)))
        item["save_pct"] = save_pct
        item["is_base"] = coins == base_coins
        out.append(item)
    return out


@app.route("/landing")
@app.route("/home")
def landing_page():
    """Landing pública (visitante)."""
    if session.get("logged_in"):
        return redirect("/leads")
    try:
        from src.trovoeda import public_stats, ensure_schema
        ensure_schema()
        stats = public_stats()
    except Exception:
        stats = {"leads_total": 0, "leads_raio": 0, "cities": 0, "niches": 0}
    return render_template(
        "landing.html",
        packages=_packages_for_view(),
        stats=stats,
    )


@app.route("/termos")
def page_termos():
    return render_template("termos.html")


@app.route("/privacidade")
def page_privacidade():
    return render_template("privacidade.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    """Cadastro público + bônus de Trovoedas (1 conta por IP)."""
    if session.get("logged_in"):
        return redirect("/")
    form = {
        "display_name": "",
        "username": "",
        "email": "",
        "whatsapp": "",
        "terms": False,
    }
    error = None
    if request.method == "POST":
        form["display_name"] = (request.form.get("display_name") or "").strip()
        form["username"] = (request.form.get("username") or "").strip().lower()
        form["email"] = (request.form.get("email") or "").strip().lower()
        form["whatsapp"] = (request.form.get("whatsapp") or "").strip()
        form["terms"] = bool(request.form.get("terms"))
        password = request.form.get("password") or ""
        client_ip = _client_ip()

        if not form["terms"]:
            error = "Aceite os Termos e a Privacidade para continuar."
        elif len(password) < 8:
            error = "Senha deve ter no mínimo 8 caracteres."
        elif not form["username"] or not form["email"] or not form["whatsapp"]:
            error = "Preencha nome de usuário, e-mail e WhatsApp."
        elif form["username"] in ("patrao", "admin", "root", "sistema"):
            error = "Este nome de usuário não está disponível."
        else:
            try:
                from src.users import (
                    create_user,
                    get_user_by_username,
                    count_users_by_signup_ip,
                    normalize_signup_ip,
                )
                from src.trovoeda import ensure_schema as t_schema
                t_schema()
                ip_n = normalize_signup_ip(client_ip)
                # Anti multi-conta: 1 cadastro público por IP
                if ip_n and count_users_by_signup_ip(ip_n) > 0:
                    error = (
                        "Já existe uma conta criada nesta rede/dispositivo. "
                        "Entre com a conta existente ou fale com o suporte se precisar de ajuda."
                    )
                elif get_user_by_username(form["username"]):
                    error = "Usuário já existe. Escolha outro login."
                else:
                    import psycopg2
                    # check email
                    try:
                        conn = psycopg2.connect(os.getenv("DATABASE_URL") or "")
                        cur = conn.cursor()
                        cur.execute(
                            "SELECT 1 FROM app_users WHERE lower(COALESCE(email,'')) = %s LIMIT 1;",
                            (form["email"],),
                        )
                        if cur.fetchone():
                            error = "Este e-mail já está cadastrado."
                        cur.close()
                        conn.close()
                    except Exception:
                        pass
                    if not error:
                        user = create_user(
                            username=form["username"],
                            password=password,
                            monthly_quota=0,  # SaaS: usa Trovoedas, não cota mensal
                            label=form["display_name"] or form["username"],
                            email=form["email"],
                            whatsapp=form["whatsapp"],
                            display_name=form["display_name"],
                            terms_accepted=True,
                            welcome_bonus=True,
                            signup_ip=ip_n,
                            enforce_ip_limit=True,
                        )
                        # login automático
                        session.clear()
                        session.permanent = True
                        session["logged_in"] = True
                        session["login_at"] = datetime.now().isoformat()
                        session["username"] = user.get("username")
                        session["role"] = "client"
                        session["label"] = user.get("label") or form["display_name"]
                        session["user_id"] = user.get("id")
                        try:
                            app.logger.info(
                                "register ok user=%s ip=%s",
                                user.get("username"),
                                ip_n or "?",
                            )
                        except Exception:
                            pass
                        return redirect("/shop?welcome=1")
            except Exception as exc:
                msg = str(exc)
                if "já existe uma conta" in msg.lower() or "rede/dispositivo" in msg.lower():
                    error = msg
                elif "unique" in msg.lower() or "duplicate" in msg.lower():
                    error = "Usuário ou e-mail já cadastrado."
                else:
                    error = "Não foi possível criar a conta. Tente de novo."
                    try:
                        app.logger.warning("register fail: %s", exc)
                    except Exception:
                        pass
    return render_template("register.html", error=error, form=form)


@app.route("/login", methods=["GET", "POST"])
@app.route("/entrar", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect("/")
    error = None
    if request.method == "POST":
        ip = _client_ip()
        if _login_rate_limited(ip):
            return render_template(
                "login.html",
                error="Muitas tentativas. Aguarde alguns minutos e tente de novo.",
            ), 429
        username = request.form.get("username") or ""
        password = request.form.get("password") or ""
        user = None
        try:
            from src.users import authenticate
            user = authenticate(username, password)
        except Exception:
            user = None
        # fallback env só se DASHBOARD_PASS estiver setada (sem default no código)
        if (
            not user
            and DASHBOARD_PASS
            and (username or "").lower() == (DASHBOARD_USER or "").lower()
            and password == DASHBOARD_PASS
        ):
            user = {
                "id": 0,
                "username": (DASHBOARD_USER or "patrao").lower(),
                "role": "admin",
                "monthly_quota": 9999,
                "label": "Patrão",
            }
        if user:
            session.clear()
            session.permanent = True  # cookie até SESSION_DAYS ou até /logout
            session["logged_in"] = True
            session["login_at"] = datetime.now().isoformat()
            # limpa flags de impersonate (segurança)
            session.pop("impersonating", None)

            raw_uname = (user.get("username") or "").strip()
            uname_n = _norm_username(raw_uname)
            # principal SEMPRE como "patrao" canônico
            if uname_n == "patrao":
                session["username"] = "patrao"
                session["role"] = "admin"
                session["label"] = user.get("label") or "Patrão"
                uid = user.get("id")
                try:
                    uid_int = int(uid) if uid not in (None, "", 0, "0") else 0
                except (TypeError, ValueError):
                    uid_int = 0
                if not uid_int:
                    try:
                        from src.users import get_user_by_username
                        db_u = get_user_by_username("patrao") or {}
                        uid_int = int(db_u.get("id") or 0)
                    except Exception:
                        uid_int = 0
                session["user_id"] = uid_int or user.get("id")
            else:
                session["username"] = raw_uname
                role = (user.get("role") or "client").lower()
                # nunca promover client a admin na sessão
                if role == "admin":
                    role = "client"
                session["role"] = role
                session["label"] = user.get("label") or raw_uname
                session["user_id"] = user.get("id")
            return redirect("/")
        _login_register_fail(ip)
        error = "Usuário ou senha incorretos."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    """Único lugar que encerra a sessão — reentrar no link mantém login até aqui."""
    session.clear()
    resp = redirect("/login")
    # apaga cookie de sessão no browser
    resp.delete_cookie(
        app.config.get("SESSION_COOKIE_NAME") or "prospecthor_session",
        path=app.config.get("SESSION_COOKIE_PATH") or "/",
    )
    return resp


@app.route("/")
def root_or_landing():
    """Visitante → landing; logado → painel."""
    if session.get("logged_in"):
        return render_template("index.html")
    return landing_page()


@app.route("/leads")
@app.route("/lead/<int:lead_id>")
@app.route("/reports")
@app.route("/settings")
@app.route("/users")
@app.route("/audit")
@app.route("/bot")
@app.route("/shop")
@app.route("/trovoedas")
@app.route("/wallet")
@app.route("/carteira")
@app.route("/orders")
@app.route("/pedidos")
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
            from src.trovoeda import get_balance
            full = get_user_by_id(int(u["id"])) or {}
            payload["monthly_quota"] = full.get("monthly_quota", 0)
            payload["assigned_this_month"] = count_assigned_this_month(int(u["id"]))
            payload["label"] = full.get("label") or u.get("username")
            payload["trovoedas"] = int(full.get("trovoedas_balance") or get_balance(int(u["id"])) or 0)
            payload["trovoedas_infinite"] = False
            if imp:
                payload["impersonate_label"] = payload["label"]
        except Exception:
            payload.setdefault("trovoedas", 0)
            payload.setdefault("trovoedas_infinite", False)
    elif is_adm and not imp:
        payload["label"] = "Patrão"
        # Host: saldo ilimitado (não gasta Trovoeda)
        payload["trovoedas"] = None
        payload["trovoedas_infinite"] = True
    else:
        payload.setdefault("trovoedas", 0)
        payload.setdefault("trovoedas_infinite", False)
    return jsonify(payload)


@app.route("/api/trovoeda/balance")
@login_required
def api_trovoeda_balance():
    """Saldo do usuário efetivo (cliente ou impersonate). Patrão = infinito."""
    from src.trovoeda import get_balance, ensure_schema
    ensure_schema()
    if _is_real_admin() and not _is_impersonating():
        return jsonify({
            "trovoedas": None,
            "trovoedas_infinite": True,
            "user_id": _real_session_user().get("id"),
        })
    uid = _effective_user_id()
    if not uid:
        return jsonify({"trovoedas": 0, "trovoedas_infinite": False})
    return jsonify({
        "trovoedas": get_balance(int(uid)),
        "trovoedas_infinite": False,
        "user_id": int(uid),
    })


@app.route("/api/trovoeda/ledger")
@login_required
def api_trovoeda_ledger():
    """Extrato do usuário efetivo; patrao pode passar ?user_id=."""
    from src.trovoeda import list_ledger, ensure_schema
    ensure_schema()
    uid = _effective_user_id()
    if _is_real_admin() and not _is_impersonating():
        q = request.args.get("user_id")
        if q and str(q).isdigit():
            uid = int(q)
    if not uid:
        return jsonify([])
    limit = int(request.args.get("limit") or 50)
    return jsonify(list_ledger(int(uid), limit=limit))


@app.route("/api/trovoeda/grant", methods=["POST"])
@login_required
@admin_required
def api_trovoeda_grant():
    """Patrão credita (ou debita se amount negativo) Trovoedas em um cliente."""
    if not _is_real_admin() or _is_impersonating():
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json(silent=True) or {}
    try:
        target_id = int(data.get("user_id") or 0)
        amount = int(data.get("amount") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "user_id e amount inválidos"}), 400
    if not target_id:
        return jsonify({"error": "user_id obrigatório"}), 400
    if amount == 0:
        return jsonify({"error": "amount não pode ser 0"}), 400

    from src.trovoeda import admin_grant, admin_debit, ensure_schema
    from src.users import get_user_by_id
    from src.audit import log_action

    ensure_schema()
    target = get_user_by_id(target_id)
    if not target:
        return jsonify({"error": "Usuário não encontrado"}), 404

    su = _session_user()
    note = (data.get("note") or "").strip()
    if amount > 0:
        result = admin_grant(
            target_id, amount, created_by=su.get("id"), note=note
        )
    else:
        result = admin_debit(
            target_id, abs(amount), created_by=su.get("id"), note=note
        )

    if not result.get("ok"):
        return jsonify({"error": result.get("error") or "falha", **result}), 400

    log_action(
        "trovoeda_grant",
        user_id=su.get("id"),
        username=su.get("username"),
        details={
            "target_id": target_id,
            "target": target.get("username"),
            "amount": amount,
            "balance": result.get("balance"),
            "note": note,
        },
    )
    return jsonify({
        "success": True,
        "user_id": target_id,
        "username": target.get("username"),
        "amount": amount,
        "trovoedas": result.get("balance"),
    })


@app.route("/api/trovoeda/packages")
@login_required
def api_trovoeda_packages():
    """Lista pacotes com preço formatado e % de economia vs base."""
    return jsonify(_packages_for_view())


# ─── Pedidos de leads (cliente pede → host aprova) ───────────────────────────

@app.route("/api/orders", methods=["GET"])
@login_required
def api_orders_list():
    """Lista pedidos: cliente vê os dele; host vê todos (ou ?status=pending)."""
    from src.orders import list_orders, count_pending, ensure_schema
    ensure_schema()
    status = (request.args.get("status") or "").strip().lower() or None
    limit = int(request.args.get("limit") or 50)
    if _is_real_admin() and not _is_impersonating():
        rows = list_orders(user_id=None, status=status, limit=limit)
        return jsonify({
            "orders": rows,
            "pending_count": count_pending(),
            "scope": "all",
        })
    uid = _effective_user_id()
    if not uid:
        return jsonify({"error": "sem usuário"}), 400
    rows = list_orders(user_id=int(uid), status=status, limit=limit)
    return jsonify({"orders": rows, "pending_count": 0, "scope": "mine"})


@app.route("/api/orders/pending-count")
@login_required
def api_orders_pending_count():
    if not _is_real_admin() or _is_impersonating():
        return jsonify({"pending_count": 0})
    from src.orders import count_pending, ensure_schema
    ensure_schema()
    return jsonify({"pending_count": count_pending()})


@app.route("/api/orders", methods=["POST"])
@login_required
def api_orders_create():
    """Cliente cria pedido de N leads (reserva Trovoedas)."""
    if _is_real_admin() and not _is_impersonating():
        # Patrão não pede leads pra si — pode impersonate se quiser testar
        return jsonify({"error": "Conta do host não cria pedidos. Entre como cliente ou use impersonate."}), 400

    data = request.get_json(silent=True) or {}
    try:
        qty = int(data.get("quantity") or data.get("qty") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "quantity inválida"}), 400
    niche = (data.get("niche") or "").strip()
    city = (data.get("city") or "").strip()
    notes = (data.get("notes") or data.get("note") or "").strip()
    # Abaixo de 10: só estoque aleatório (sem cidade/nicho)
    if qty < 10:
        niche = ""
        city = ""

    uid = _effective_user_id()
    if not uid:
        return jsonify({"error": "sem usuário"}), 400

    from src.orders import create_order, ensure_schema
    from src.audit import log_action
    ensure_schema()
    result = create_order(
        int(uid),
        qty,
        niche=niche,
        city=city,
        notes=notes,
    )
    if not result.get("ok"):
        return jsonify({"error": result.get("error") or "falha", "balance": result.get("balance")}), 400

    actor_id, actor_name = _audit_actor()
    log_action(
        "order_create",
        user_id=actor_id,
        username=actor_name,
        details={
            "order_id": (result.get("order") or {}).get("id"),
            "quantity": qty,
            "city": city,
            "niche": niche,
        },
    )
    _invalidate_cache()
    return jsonify(result)


@app.route("/api/orders/<int:order_id>/cancel", methods=["POST"])
@login_required
def api_orders_cancel(order_id: int):
    uid = _effective_user_id()
    if not uid:
        return jsonify({"error": "sem usuário"}), 400
    from src.orders import cancel_order, ensure_schema
    from src.audit import log_action
    ensure_schema()
    result = cancel_order(int(order_id), int(uid))
    if not result.get("ok"):
        return jsonify({"error": result.get("error") or "falha"}), 400
    actor_id, actor_name = _audit_actor()
    log_action(
        "order_cancel",
        user_id=actor_id,
        username=actor_name,
        details={"order_id": order_id},
    )
    return jsonify(result)


@app.route("/api/orders/<int:order_id>/approve", methods=["POST"])
@admin_required
def api_orders_approve(order_id: int):
    data = request.get_json(silent=True) or {}
    host_note = (data.get("host_note") or data.get("note") or "").strip()
    auto = data.get("auto_deliver")
    if auto is None:
        auto_deliver = True
    else:
        auto_deliver = bool(auto)

    from src.orders import approve_order, ensure_schema
    from src.audit import log_action
    ensure_schema()
    host = _real_session_user()
    result = approve_order(
        int(order_id),
        host_id=host.get("id"),
        host_note=host_note,
        auto_deliver=auto_deliver,
    )
    if not result.get("ok"):
        return jsonify({"error": result.get("error") or "falha"}), 400
    actor_id, actor_name = _audit_actor()
    log_action(
        "order_approve",
        user_id=actor_id,
        username=actor_name,
        details={
            "order_id": order_id,
            "delivered_now": result.get("delivered_now"),
            "host_note": host_note,
        },
    )
    _invalidate_cache()
    return jsonify(result)


@app.route("/api/orders/<int:order_id>/reject", methods=["POST"])
@admin_required
def api_orders_reject(order_id: int):
    data = request.get_json(silent=True) or {}
    host_note = (data.get("host_note") or data.get("note") or data.get("reason") or "").strip()

    from src.orders import reject_order, ensure_schema
    from src.audit import log_action
    ensure_schema()
    host = _real_session_user()
    result = reject_order(
        int(order_id),
        host_id=host.get("id"),
        host_note=host_note or "Recusado pelo host",
    )
    if not result.get("ok"):
        return jsonify({"error": result.get("error") or "falha"}), 400
    actor_id, actor_name = _audit_actor()
    log_action(
        "order_reject",
        user_id=actor_id,
        username=actor_name,
        details={"order_id": order_id, "host_note": host_note},
    )
    return jsonify(result)


@app.route("/api/orders/<int:order_id>/fulfill", methods=["POST"])
@admin_required
def api_orders_fulfill(order_id: int):
    """Host entrega mais leads do pool para pedido já aprovado."""
    data = request.get_json(silent=True) or {}
    max_leads = data.get("max_leads") or data.get("quantity")
    try:
        max_leads = int(max_leads) if max_leads is not None else None
    except (TypeError, ValueError):
        max_leads = None

    from src.orders import fulfill_order, ensure_schema
    from src.audit import log_action
    ensure_schema()
    host = _real_session_user()
    result = fulfill_order(
        int(order_id),
        host_id=host.get("id"),
        max_leads=max_leads,
    )
    if not result.get("ok"):
        return jsonify({"error": result.get("error") or "falha"}), 400
    actor_id, actor_name = _audit_actor()
    log_action(
        "order_fulfill",
        user_id=actor_id,
        username=actor_name,
        details={
            "order_id": order_id,
            "delivered_now": result.get("delivered_now"),
        },
    )
    _invalidate_cache()
    return jsonify(result)


@app.route("/api/trovoeda/checkout", methods=["POST"])
@login_required
def api_trovoeda_checkout():
    """
    Inicia checkout Stripe do pacote.
    Fase atual: retorna 501 se Stripe não configurado (UI da loja já funciona).
    """
    data = request.get_json(silent=True) or {}
    slug = (data.get("package") or data.get("slug") or "").strip().lower()
    if not slug:
        return jsonify({"error": "package obrigatório"}), 400

    from src.trovoeda import list_packages, ensure_schema
    ensure_schema()
    pkgs = {p.get("slug"): p for p in list_packages(active_only=True)}
    pkg = pkgs.get(slug)
    if not pkg:
        return jsonify({"error": "pacote não encontrado"}), 404

    secret = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    price_id = (pkg.get("stripe_price_id") or "").strip() or (
        os.getenv(f"STRIPE_PRICE_{slug.upper()}") or ""
    ).strip()

    if not secret or not price_id:
        return jsonify({
            "status": "not_configured",
            "message": "Stripe ainda não configurado",
            "package": pkg,
        }), 501

    # Stripe real — fases seguintes; mantém stub seguro
    try:
        import stripe  # type: ignore
        stripe.api_key = secret
        uid = _effective_user_id()
        success = request.host_url.rstrip("/") + "/shop?paid=1"
        cancel = request.host_url.rstrip("/") + "/shop?canceled=1"
        session_obj = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success,
            cancel_url=cancel,
            client_reference_id=str(uid or ""),
            metadata={
                "user_id": str(uid or ""),
                "package": slug,
                "coins": str(pkg.get("coins") or 0),
            },
        )
        return jsonify({"url": session_obj.url, "session_id": session_obj.id})
    except ImportError:
        return jsonify({
            "status": "not_configured",
            "message": "Pacote stripe não instalado no servidor",
            "package": pkg,
        }), 501
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


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
            elif status in ("novo", "recuperar"):
                # Volta pro funil: limpa marcador Descartado/Convertido e contacted_at
                cur.execute(
                    """
                    UPDATE companies SET
                        notes = CASE
                            WHEN LOWER(TRIM(COALESCE(notes, ''))) IN ('descartado', 'convertido')
                            THEN NULL
                            ELSE notes
                        END,
                        contacted_at = NULL
                    WHERE id = %s
                    """,
                    (lead_id,),
                )
            log_action(
                f"status_{status}",
                user_id=audit_uid,
                username=audit_uname,
                lead_id=lead_id,
                company_name=lead.get("name"),
                details={"status": status, "impersonating": _is_impersonating()},
            )
        # notas manuais: não sobrescreve se o body só veio com status (evita re-descartar no recover)
        if "notes" in data and data["notes"] is not None and status not in ("novo", "recuperar"):
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

    def _is_score_meta_problem(text: str) -> bool:
        """Ignora lixo de cálculo interno (ex.: Blocos /100: dor=…·cap=…)."""
        s = (text or "").strip().lower()
        if not s:
            return True
        if s.startswith("blocos /100") or s.startswith("blocos/100"):
            return True
        if "dor=" in s and ("cap=" in s or "vis=" in s):
            return True
        if "→" in s and "/100" in s and ("dor" in s or "bloco" in s):
            return True
        if s.startswith("teto 58") or s.startswith("teto 62"):
            return True
        if "score usa dor" in s:
            return True
        return False

    problemas = {}
    contactados = 0
    convertidos = 0
    for l in filtered:
        try:
            for p in json.loads(l.get("lead_problems") or "[]"):
                if _is_score_meta_problem(str(p)):
                    continue
                # normaliza label: corta detalhe após " — " ou " (+N)"
                raw = str(p).strip()
                k = raw.split(" (")[0].split(" — ")[0].split(" – ")[0].strip()
                if not k or _is_score_meta_problem(k):
                    continue
                problemas[k] = problemas.get(k, 0) + 1
        except Exception:
            pass
        notes = (l.get("notes") or "").lower()
        if l.get("contacted_at"):
            contactados += 1
        if notes == "convertido":
            convertidos += 1

    # top sinais reais (sem meta de score)
    problemas_sorted = dict(
        sorted(problemas.items(), key=lambda kv: (-kv[1], kv[0]))[:12]
    )

    total = len(filtered)
    return jsonify({
        "total": total,
        "quentes": total,
        "mornos": contactados,
        "frios": 0,
        "contactados": contactados,
        "conversao_taxa": round((convertidos / max(contactados, 1)) * 100, 1),
        "problemas_comuns": problemas_sorted,
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
    from src.users import lead_has_client_contact
    aid = data.get("assigned_to")
    lead = get_lead_by_id(lead_id) or {}
    if aid in (None, "", 0, "0", "null"):
        ok = manual_assign(lead_id, None)
        assigned = None
    else:
        if not lead_has_client_contact(lead):
            return jsonify({
                "success": False,
                "error": "Lead sem WhatsApp/telefone nem Instagram — não envia para cliente.",
            }), 400
        ok = manual_assign(lead_id, int(aid))
        if not ok:
            return jsonify({
                "success": False,
                "error": "Não foi possível atribuir (sem contato ou erro).",
            }), 400
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


@app.route("/api/leads/distribute", methods=["POST"])
@login_required
@admin_required
def api_leads_distribute():
    """
    Patrão distribui sobras.

    Modo seletivo (preferido):
      { "lead_ids": [1,2,3], "user_id": 5, "respect_quota": true }

    Modo automático (legado):
      { "limit": N, "user_id": id? } — round-robin em clientes ativos com vaga.
    """
    from src.users import distribute_free_leads, distribute_selected_leads
    from src.audit import log_action
    data = request.get_json(silent=True) or {}
    lead_ids = data.get("lead_ids") or data.get("ids") or []
    if isinstance(lead_ids, (int, str)):
        lead_ids = [lead_ids]

    # —— seleção manual: leads escolhidos + destinatário ——
    if lead_ids and data.get("user_id") not in (None, "", "null"):
        try:
            only = int(data.get("user_id"))
        except (TypeError, ValueError):
            return jsonify({"error": "user_id inválido"}), 400
        respect = data.get("respect_quota", True)
        if isinstance(respect, str):
            respect = respect.lower() in ("1", "true", "yes", "sim")
        result = distribute_selected_leads(
            lead_ids=[int(x) for x in lead_ids if str(x).strip().isdigit() or isinstance(x, int)],
            user_id=only,
            respect_quota=bool(respect),
        )
        audit_uid, audit_uname = _audit_actor()
        log_action(
            "distribute_selected",
            user_id=audit_uid,
            username=audit_uname,
            details={
                "distributed": result.get("distributed"),
                "skipped": result.get("skipped"),
                "lead_ids_count": len(lead_ids),
                "user_id": only,
                "by_user": result.get("by_user"),
            },
        )
        _invalidate_cache()
        ok = int(result.get("distributed") or 0) > 0
        return jsonify({"success": ok, **result}), (200 if ok or result.get("message") else 400)

    # —— automático ——
    limit = data.get("limit")
    only_uid = data.get("user_id")
    try:
        lim = int(limit) if limit not in (None, "", "null") else None
    except (TypeError, ValueError):
        lim = None
    try:
        only = int(only_uid) if only_uid not in (None, "", "null", 0, "0") else None
    except (TypeError, ValueError):
        only = None

    result = distribute_free_leads(limit=lim, only_user_id=only)
    audit_uid, audit_uname = _audit_actor()
    log_action(
        "distribute_free",
        user_id=audit_uid,
        username=audit_uname,
        details={
            "distributed": result.get("distributed"),
            "remaining_free": result.get("remaining_free"),
            "skipped": result.get("skipped"),
            "by_user": result.get("by_user"),
            "limit": lim,
            "only_user_id": only,
        },
    )
    _invalidate_cache()
    return jsonify({"success": True, **result})


@app.route("/api/leads/<int:lead_id>", methods=["DELETE"])
@app.route("/api/leads/<int:lead_id>/delete", methods=["POST"])
@login_required
@admin_required
def api_lead_delete(lead_id):
    """Patrão apaga um lead do banco (de qualquer dono). Cliente = 403."""
    from src.users import delete_company_lead
    from src.audit import log_action

    lead = get_lead_by_id(lead_id) or {}
    if not lead:
        return jsonify({"error": "Lead não encontrado"}), 404

    ok = delete_company_lead(int(lead_id))
    if not ok:
        return jsonify({"error": "Não foi possível apagar o lead"}), 500

    audit_uid, audit_uname = _audit_actor()
    log_action(
        "lead_delete",
        user_id=audit_uid,
        username=audit_uname,
        lead_id=lead_id,
        company_name=lead.get("name"),
        details={
            "assigned_to": lead.get("assigned_to"),
            "city": lead.get("city"),
            "niche": lead.get("niche"),
        },
    )
    _invalidate_cache()
    return jsonify({"success": True, "deleted_id": lead_id, "name": lead.get("name")})


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


@app.route("/api/bot-plan", methods=["GET"])
@login_required
@admin_required
def api_bot_plan_get():
    """Plano de busca (meta leads + cidades + nichos) + catálogo."""
    from src.bot_plan import get_plan, ensure_schema
    ensure_schema()
    return jsonify(get_plan())


@app.route("/api/bot-plan", methods=["POST", "PUT"])
@login_required
@admin_required
def api_bot_plan_save():
    """Salva plano do robô a partir do painel."""
    from src.bot_plan import save_plan, ensure_schema
    from src.audit import log_action
    ensure_schema()
    data = request.get_json(silent=True) or {}
    try:
        target = int(data.get("target_leads") if data.get("target_leads") is not None else 20)
    except (TypeError, ValueError):
        target = 20
    city_ids = data.get("city_ids") or []
    niche_ids = data.get("niche_ids") or []
    if not isinstance(city_ids, list):
        city_ids = []
    if not isinstance(niche_ids, list):
        niche_ids = []
    notes = str(data.get("notes") or "")
    su = _session_user()
    plan = save_plan(
        target_leads=target,
        city_ids=[str(x) for x in city_ids],
        niche_ids=[str(x) for x in niche_ids],
        notes=notes,
        updated_by=su.get("username") or "patrao",
    )
    log_action(
        "bot_plan_save",
        user_id=su.get("id"),
        username=su.get("username"),
        details={
            "target_leads": target,
            "city_ids": city_ids,
            "niche_ids": niche_ids,
        },
    )
    return jsonify({"success": True, "plan": plan})


@app.route("/api/bot-status")
@login_required
@admin_required
def api_bot_status():
    from src.bot_status import get_status, ensure_schema
    ensure_schema()
    return jsonify(get_status(log_limit=int(request.args.get("limit") or 15)))


@app.route("/api/bot-status/stop", methods=["POST"])
@login_required
@admin_required
def api_bot_status_stop():
    """Força status = parado (quando o bot morreu e ficou 'rodando' no painel)."""
    from src.bot_status import force_parado, ensure_schema
    from src.audit import log_action
    ensure_schema()
    data = request.get_json(silent=True) or {}
    reason = (data.get("reason") or "marcado parado pelo painel").strip()[:200]
    st = force_parado(reason=reason)
    su = _session_user()
    log_action(
        "bot_force_stop",
        user_id=su.get("id"),
        username=su.get("username"),
        details={"reason": reason},
    )
    _invalidate_cache()
    return jsonify({"success": True, "status": st})


@app.route("/api/settings")
@login_required
def api_settings():
    return jsonify({"niches": [], "cities": []})


handler = app
