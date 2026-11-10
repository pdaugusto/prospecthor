"""Green presentation over the existing application services and permissions.

No credentials, demo records or business mutations belong to this renderer.
Set PROSPECTHOR_UI=legacy to render the original templates again.
"""
import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from flask import abort, current_app, redirect, request, session
from werkzeug.exceptions import HTTPException
from src.lead_metrics import lead_status, received_at, select_report_period, public_signals, REPORT_ZONE


def json_list(value):
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def safe_url(value):
    text = str(value or "").strip()
    try:
        parsed = urlsplit(text)
    except ValueError:
        return ""
    return text if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def phone_url(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return "tel:+" + (digits if digits.startswith("55") else "55" + digits) if digits else ""


def contact_url(lead):
    from src.contact import wa_link, extract_instagram
    whatsapp = wa_link(lead.get("phone"))
    instagram, _ = extract_instagram(lead)
    return whatsapp or safe_url(instagram)


def brl(value):
    return f"{float(value or 0):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def lead_summary(lead):
    status_labels={"novo":"Novo","contactado":"Contactado","convertido":"Convertido","descartado":"Descartado","reposto":"Reposto"}
    lines=[f"ProspecTHOR — {lead.get('name') or 'Empresa'}",f"Score: {lead.get('lead_score') or 0}/100",
           f"Cidade: {lead.get('city') or 'Não informada'} / {lead.get('state') or ''}",
           f"Contato: {lead.get('phone') or 'Não informado'}",
           f"Site: {lead.get('website') or lead.get('website_status') or 'Não informado'}",
           f"Etapa: {status_labels[lead_status(lead)]}","","Sinais da oportunidade:"]
    lines += ["- "+item for item in public_signals(lead.get("lead_problems"))] or ["- Sem sinais detalhados registrados."]
    lines += ["","Serviços sugeridos:"]
    lines += ["- "+str(item) for item in json_list(lead.get("lead_services"))] or ["- Avaliar a presença digital antes da proposta."]
    return "\n".join(lines)


def outreach_drafts(lead):
    name=str(lead.get("name") or "empresa")
    place=(" em "+str(lead["city"])) if lead.get("city") else ""
    message=f"Olá, equipe da {name}! Encontrei a empresa{place} e gostaria de conversar sobre sua presença digital. Trabalho com criação de sites. Posso enviar uma ideia de como um site poderia ajudar vocês?"
    return {"whatsapp":message,"instagram":f"Olá, equipe da {name}! Trabalho com criação de sites e gostaria de apresentar uma ideia para a presença digital de vocês. Podemos conversar?",
            "email":f"Assunto: Uma ideia para a presença digital da {name}\n\n{message}\n\nObrigado!"}


def metrics(leads, days=7):
    counts = Counter(lead_status(lead) for lead in leads)
    niches = Counter((lead.get("niche") or "Sem nicho") for lead in leads)
    dates = [(datetime.now(REPORT_ZONE).date() - timedelta(days=n)) for n in range(days-1, -1, -1)]
    daily = Counter(value.date().isoformat() for lead in leads if (value := received_at(lead)))
    series = [daily[date.isoformat()] for date in dates]
    peak = max(series, default=0) or 1
    points = [(282 if days==1 else 24+index*516/(days-1),152-value/peak*120) for index,value in enumerate(series)]
    path = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in points)
    marks = sorted({round(index*(days-1)/6) for index in range(7)})
    return {
        "total": len(leads), "counts": counts, "niches": niches.most_common(6),
        "priority": sum(int(lead.get("lead_score") or 0) >= 80 for lead in leads),
        "favorite": sum(bool(lead.get("is_favorite")) for lead in leads),
        "series": series, "dates": [date.strftime("%d/%m") for date in dates], "days":days,
        "axis_labels":[{"x":points[index][0],"date":dates[index].strftime("%d/%m"),"value":series[index]} for index in marks],
        "points":points,"path":path,"area":path+" L 540 178 L 24 178 Z" if days>1 else "",
        "top": sorted(leads, key=lambda lead: int(lead.get("lead_score") or 0), reverse=True)[:5],
    }


def install_green_ui(app, original_render, api):
    @app.after_request
    def green_private_html_cache(response):
        # The green UI now contains account data in HTML, not only API JSON.
        if not request.path.startswith("/static/") and (session.get("logged_in") or request.path.startswith(("/login","/entrar","/register","/reset/","/esqueci"))):
            response.headers["Cache-Control"] = "private, no-store, no-cache, must-revalidate"
            response.headers.add("Vary", "Cookie")
        return response

    def result(endpoint, **kwargs):
        response = app.make_response(app.view_functions[endpoint](**kwargs))
        if response.status_code >= 400:
            abort(response.status_code)
        if not response.is_json:
            abort(503)
        return response.get_json()

    def context():
        from src.contact import wa_link
        me = result("api_me")
        return {"me": me, "admin": bool(me.get("is_admin")),
                "icon": "/static/green/cliente/icons.svg",
                "lead_status": lead_status, "json_list": json_list, "safe_url": safe_url,
                "brl": brl, "request": request, "wa_link": wa_link,
                "ledger_labels": {"purchase":"Compra confirmada","reserve":"Reserva de pedido","release":"Reserva devolvida","spend":"Entrega de leads","admin_grant":"Crédito da operação","admin_debit":"Débito da operação","welcome_bonus":"Bônus de boas-vindas"},
                "contact_url": contact_url, "phone_url": phone_url,
                "status_labels": {"novo":"Novo", "contactado":"Contactado", "convertido":"Convertido", "descartado":"Descartado", "reposto":"Reposto"},
                "order_labels": {"pending":"Aguardando aprovação", "approved":"Em entrega", "fulfilled":"Entregue", "cancelled":"Cancelado", "rejected":"Recusado"}}

    paths = {
        "/": ("home", "Início"), "/leads": ("leads", "Meus leads"),
        "/salvos": ("saved", "Leads salvos"), "/quadro": ("board", "Negociações"),
        "/board": ("board", "Negociações"), "/reports": ("reports", "Relatórios"),
        "/shop": ("shop", "Loja de Trovoedas"), "/trovoedas": ("shop", "Loja de Trovoedas"),
        "/wallet": ("wallet", "Carteira"), "/carteira": ("wallet", "Carteira"),
        "/orders": ("orders", "Meus pedidos"), "/pedidos": ("orders", "Meus pedidos"),
        "/pedir": ("request", "Pedir leads"), "/checkout": ("checkout", "Revisar recarga"),
        "/pagamento": ("payment", "Pagamento"), "/plano": ("plan", "Meu plano"),
        "/conta": ("account", "Minha conta"), "/settings": ("account", "Minha conta"),
        "/ajuda": ("help", "Ajuda"), "/notificacoes": ("notifications", "Notificações"),
        "/compras": ("purchases", "Minhas compras"), "/users": ("users", "Clientes e planos"),
        "/audit": ("audit", "Auditoria"), "/bot": ("bot", "Operação do robô"),
        "/admin/leads": ("pool", "Pool de leads"), "/admin/pedidos": ("admin_orders", "Pedidos dos clientes"),
        "/admin/ocorrencias": ("reports_admin", "Atendimento e reportes"),
    }
    admin_pages = {"users", "audit", "bot", "pool", "admin_orders", "reports_admin"}

    def dashboard(page=None, item_id=None):
        page, title = (page, "Ficha do lead" if page == "lead" else "Detalhes do pedido") if page else paths.get(request.path, ("home", "Início"))
        ctx = context()
        if page in admin_pages and not ctx["admin"]:
            abort(403)
        ctx.update(page=page, title=title)
        try:
            if page in {"home", "leads", "saved", "board", "reports"}:
                leads = result("api_leads")
                ctx.update(leads=leads, metrics=metrics(leads))
                if page == "reports":
                    try:
                        report=select_report_period(leads,request.args.get("period") or "weekly")
                    except ValueError:
                        abort(400)
                    ctx.update(report=report,leads=report["leads"],metrics=metrics(report["leads"],report["days"]))
            if page == "lead":
                ctx["lead"] = result("api_lead_detail", lead_id=item_id)
                ctx.update(summary=lead_summary(ctx["lead"]),drafts=outreach_drafts(ctx["lead"]))
            if page in {"shop", "checkout", "plan", "users"}:
                ctx["packages"] = api["_packages_for_view"]()
            if page == "checkout":
                slug = request.args.get("package", "")
                package = next((p for p in ctx["packages"] if p["slug"] == slug and not p.get("daily_cap")), None)
                if not package:
                    abort(404)
                ctx["package"] = package
            if page in {"orders", "admin_orders", "notifications", "help"}:
                ctx["orders"] = result("api_orders_list")["orders"]
            if page in {"wallet", "purchases", "notifications", "payment"}:
                ctx["ledger"] = result("api_trovoeda_ledger")
            if page == "order":
                from src.orders import get_order
                order = get_order(item_id)
                if not order or (not ctx["admin"] and int(order.get("user_id") or 0) != int(ctx["me"].get("id") or 0)):
                    abort(404)
                ctx["order"] = order
            if page == "account":
                from src.users import get_user_by_id
                ctx["profile"] = get_user_by_id(int(ctx["me"].get("id") or 0)) or {}
            if page == "request":
                from src.bot_plan import load_catalog
                ctx["catalog"] = load_catalog()
            if page == "users":
                ctx["users"] = result("api_users_list")
            if page in {"audit", "reports_admin"}:
                try:
                    api["_audit_filter_values"]()
                except ValueError as error:
                    ctx.update(audit=[],filter_error=str(error))
                else:
                    ctx["audit"] = result("api_audit")
            if page == "pool":
                leads = api["get_all_leads"](scope="all", owner_id=None)
                ctx.update(leads=leads, metrics=metrics(leads), users=result("api_users_list"))
            if page == "bot":
                ctx["bot"] = result("api_bot_status")
                ctx["bot_plan"] = result("api_bot_plan_get")
        except HTTPException as error:
            if error.code == 503:
                return original_render("green/error.html", **ctx), 503
            raise
        except Exception:
            current_app.logger.exception("Não foi possível carregar a tela %s", page)
            return original_render("green/error.html", **ctx), 503
        return original_render("green/dashboard.html", **ctx), (400 if ctx.get("filter_error") else 200)

    def render(template_name, **kwargs):
        if os.getenv("PROSPECTHOR_UI", "green").lower() == "legacy":
            return original_render(template_name, **kwargs)
        if template_name == "index.html":
            if request.path.startswith("/lead/"):
                return dashboard("lead", int(request.view_args["lead_id"]))
            return dashboard()
        if template_name in {"login.html", "register.html", "forgot.html", "reset.html",
                             "landing.html", "termos.html", "privacidade.html"}:
            return original_render("green/" + template_name, **kwargs)
        return original_render(template_name, **kwargs)

    for path in ("/salvos", "/pedir", "/checkout", "/pagamento", "/plano", "/conta",
                 "/ajuda", "/notificacoes", "/compras", "/admin/leads",
                 "/admin/pedidos", "/admin/ocorrencias"):
        endpoint = "green_" + path.strip("/").replace("/", "_")
        app.add_url_rule(path, endpoint, api["login_required"](dashboard))
    app.add_url_rule("/pedido/<int:item_id>", "green_order",
                     api["login_required"](lambda item_id: dashboard("order", item_id)))
    app.jinja_env.filters["brl"] = brl
    app.jinja_env.globals["svg_icon"] = "/static/green/cliente/icons.svg"

    @app.post("/api/support")
    @api["login_required"]
    def green_support():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return {"error":"Solicitação inválida."}, 400
        subject = str(data.get("subject") or "").strip()[:120]
        message = str(data.get("message") or "").strip()
        if not subject or not 10 <= len(message) <= 2000:
            return {"error":"Informe o assunto e uma mensagem de 10 a 2000 caracteres."}, 400
        from src.audit import ensure_schema
        # Reuse the durable operator inbox without inventing an email delivery.
        actor_id, actor_name = api["_audit_actor"]()
        conn = None
        try:
            ensure_schema()
            conn = api["get_db"]()
            cur = conn.cursor()
            cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (19743, int(actor_id or 0)))
            cur.execute("SELECT COUNT(*) FROM audit_log WHERE action='support_request' AND user_id=%s AND created_at >= %s",
                        (actor_id, (datetime.now()-timedelta(minutes=10)).isoformat()))
            if int(cur.fetchone()[0]) >= 5:
                conn.rollback()
                return {"error":"Aguarde alguns minutos antes de enviar outra solicitação."}, 429
            cur.execute("INSERT INTO audit_log (created_at,user_id,username,action,details) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                        (datetime.now().isoformat(),actor_id,actor_name,"support_request",
                         json.dumps({"subject":subject,"message":message},ensure_ascii=False)))
            ticket = cur.fetchone()[0]
            conn.commit()
        except Exception:
            current_app.logger.exception("Falha ao registrar solicitação de suporte")
            return {"error":"Não foi possível registrar. Tente novamente."}, 503
        finally:
            if conn:
                conn.close()
        return {"success":True, "id":ticket, "message":"Solicitação registrada para a operação."}

    return render
