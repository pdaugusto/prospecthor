"""Isolated real-Postgres integration; never reads the project's .env.

Run from the integration copy with its venv Python. Own disposable database,
synthetic users/leads, Flask test client. No scraper, emails or Stripe network.
"""
import os
import sys
import tempfile
import json
import hmac
import hashlib
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Before importing project modules: don't let dotenv read unrelated credentials.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["FLASK_SECRET_KEY"] = "isolated-green-integration-not-production"
os.environ["DASHBOARD_PASS"] = "test-only-admin-password-9284"
os.environ["DB_POOL"] = "0"
os.environ["STRIPE_SECRET_KEY"] = ""
os.environ["STRIPE_API_KEY"] = ""
os.environ["SMTP_HOST"] = ""

import pgserver
import psycopg2


def main():
    directory = tempfile.mkdtemp(prefix="prospecthor-green-e2e-")
    server = pgserver.get_server(str(Path(directory) / "pgdata"), cleanup_mode="stop")
    uri = server.get_uri()
    os.environ["DATABASE_URL"] = uri
    from src.google_maps import _CREATE_TABLE_SQL
    from src.scorer import _SCORER_COLUMNS
    with psycopg2.connect(uri) as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE_TABLE_SQL)
            for column, kind in _SCORER_COLUMNS:
                cur.execute(f"ALTER TABLE companies ADD COLUMN IF NOT EXISTS {column} {kind}")
            for column, kind in [("website_status","TEXT"),("instagram_url","TEXT"),("instagram_username","TEXT"),("contacted_at","TEXT"),("notes","TEXT")]:
                cur.execute(f"ALTER TABLE companies ADD COLUMN IF NOT EXISTS {column} {kind}")
    from src import users, trovoeda
    users.ensure_schema()
    trovoeda.ensure_schema()
    first = users.create_user(username="green_alpha", password="isolated-password-alpha", monthly_quota=0, label="Alpha teste", welcome_bonus=False)
    second = users.create_user(username="green_beta", password="isolated-password-beta", monthly_quota=0, label="Beta teste", welcome_bonus=False)
    assert first["id"] != second["id"]
    assert trovoeda.admin_grant(first["id"],30,note="isolated test")["ok"]
    with psycopg2.connect(uri) as conn:
        with conn.cursor() as cur:
            for owner,name in [(first["id"],"Empresa Alpha"),(second["id"],"Empresa Beta"),(None,"Empresa Livre")]:
                cur.execute("""INSERT INTO companies (place_id,name,city,state,niche,phone,website_status,lead_class,
                    lead_score,lead_problems,assigned_to,assigned_at,scraped_at)
                    VALUES (%s,%s,'Salvador','BA','Odontologia','11987654321','sem_site','raio',88,'["Sem site"]',%s,NOW()::TEXT,NOW()::TEXT) RETURNING id""",
                    (name,name,owner))
                if owner == first["id"]: own_id=cur.fetchone()[0]
                elif owner == second["id"]: foreign_id=cur.fetchone()[0]
    from api.index import app
    app.config["TESTING"]=True
    alpha, beta, host = app.test_client(), app.test_client(), app.test_client()
    assert alpha.post("/login",data={"username":"green_alpha","password":"isolated-password-alpha"}).status_code==302
    assert beta.post("/login",data={"username":"green_beta","password":"isolated-password-beta"}).status_code==302
    assert host.post("/login",data={"username":"patrao","password":os.environ["DASHBOARD_PASS"]}).status_code==302
    own=alpha.get("/api/leads").get_json()
    assert [lead["id"] for lead in own] == [own_id],own
    assert alpha.get("/lead/"+str(foreign_id)).status_code==404
    assert alpha.post(f"/api/leads/{foreign_id}/favorite",json={"fav":True}).status_code==404
    assert alpha.get("/users").status_code==403
    assert alpha.post(f"/api/leads/{own_id}/favorite",json={"fav":True}).status_code==200
    assert alpha.get(f"/api/leads/{own_id}").get_json()["is_favorite"]
    assert alpha.post(f"/api/leads/{own_id}/status",json={"status":"contactado"}).status_code==200
    assert alpha.get(f"/api/leads/{own_id}").get_json()["contact_status"]=="contactado"
    assert alpha.post(f"/api/leads/{own_id}/status",json={"notes":"Conversa em andamento"}).status_code==200
    assert alpha.get(f"/api/leads/{own_id}").get_json()["notes"]=="Conversa em andamento"
    response=alpha.post("/api/orders",json={"quantity":5,"city":"IGNORAR","niche":"IGNORAR"})
    assert response.status_code==200,response.get_json()
    order=response.get_json()["order"]
    assert not order["city"] and not order["niche"],order
    assert alpha.get("/api/trovoeda/balance").get_json()["trovoedas"]==25
    assert beta.get("/pedido/"+str(order["id"])).status_code==404
    assert beta.post(f"/api/orders/{order['id']}/cancel",json={}).status_code==400
    assert alpha.post(f"/api/orders/{order['id']}/cancel",json={}).status_code==200
    assert alpha.get("/api/trovoeda/balance").get_json()["trovoedas"]==30
    assert alpha.post(f"/api/orders/{order['id']}/cancel",json={}).status_code==400
    assert alpha.get("/api/trovoeda/balance").get_json()["trovoedas"]==30
    assert alpha.post("/api/orders",json={"quantity":5000}).status_code==400
    insufficient=alpha.post("/api/orders",json={"quantity":40})
    assert insufficient.status_code==400,insufficient.get_json()
    assert alpha.get("/api/trovoeda/balance").get_json()["trovoedas"]==30
    checkout=alpha.post("/api/trovoeda/checkout",json={"package":"faisca"})
    assert checkout.status_code==501,checkout.get_json()
    assert alpha.get("/shop?paid=1").status_code==200
    assert alpha.get("/api/trovoeda/balance").get_json()["trovoedas"]==30
    support=alpha.post("/api/support",json={"subject":"Ajuda","message":"Solicitação isolada do teste"})
    assert support.status_code==200,support.get_json()
    logs=host.get("/api/audit").get_json()
    assert any(entry["action"]=="support_request" for entry in logs)
    # Host approval and delivery must not charge the reserved amount twice.
    pending=alpha.post("/api/orders",json={"quantity":1}).get_json()["order"]
    approved=host.post(f"/api/orders/{pending['id']}/approve",json={"auto_deliver":True})
    assert approved.status_code==200,approved.get_json()
    assert alpha.get("/api/trovoeda/balance").get_json()["trovoedas"]==29
    detail=alpha.get("/pedido/"+str(pending["id"]))
    assert detail.status_code==200
    assert len(alpha.get("/api/leads").get_json())==2
    rejected=alpha.post("/api/orders",json={"quantity":3}).get_json()["order"]
    assert host.post(f"/api/orders/{rejected['id']}/reject",json={"host_note":"isolated rejection"}).status_code==200
    assert alpha.get("/api/trovoeda/balance").get_json()["trovoedas"]==29
    # Real Stripe signature verification, but a locally signed synthetic event.
    os.environ["STRIPE_WEBHOOK_SECRET"]="whsec_isolated_only"
    event={"id":"evt_isolated_green","type":"checkout.session.completed","data":{"object":{
        "id":"cs_isolated_green","payment_status":"paid","client_reference_id":str(first["id"]),
        "metadata":{"package":"faisca","coins":"10"},"amount_total":1490,"currency":"brl"}}}
    payload=json.dumps(event)
    stamp=str(int(time.time()))
    signature=hmac.new(os.environ["STRIPE_WEBHOOK_SECRET"].encode(),(stamp+"."+payload).encode(),hashlib.sha256).hexdigest()
    headers={"Stripe-Signature":"t="+stamp+",v1="+signature,"Content-Type":"application/json"}
    assert alpha.post("/api/webhook/stripe",data=payload,headers=headers).status_code==200
    assert alpha.get("/api/trovoeda/balance").get_json()["trovoedas"]==39
    assert alpha.post("/api/webhook/stripe",data=payload,headers=headers).status_code==200
    assert alpha.get("/api/trovoeda/balance").get_json()["trovoedas"]==39
    assert alpha.post("/api/webhook/stripe",data=payload,headers={"Stripe-Signature":"invalid"}).status_code==400
    # A report can return one coin only once, with decision and ledger atomic.
    assert alpha.post(f"/api/leads/{own_id}/report",json={"reason":"numero_invalido"}).status_code==200
    report = next(row for row in host.get("/api/audit").get_json()
                  if row["action"]=="report_invalid" and row["lead_id"]==own_id)
    decision = host.post(f"/api/leads/{own_id}/resolve-report",json={"approve":True,"report_id":report["id"]})
    assert decision.status_code==200,decision.get_json()
    assert alpha.get("/api/trovoeda/balance").get_json()["trovoedas"]==40
    assert host.post(f"/api/leads/{own_id}/resolve-report",json={"approve":True,"report_id":report["id"]}).status_code==409
    assert host.post(f"/api/leads/{own_id}/resolve-report",json={"approve":True}).status_code==409
    assert alpha.get("/api/trovoeda/balance").get_json()["trovoedas"]==40
    assert host.post(f"/api/leads/{foreign_id}/resolve-report",json={"approve":True}).status_code==404
    assert alpha.post(f"/api/leads/{foreign_id}/report",json={}).status_code==404
    assert beta.post(f"/api/leads/{foreign_id}/report",json={}).status_code==200
    assert host.post(f"/api/leads/{foreign_id}/resolve-report",json={"approve":False}).status_code==200
    assert host.post(f"/api/leads/{foreign_id}/resolve-report",json={"approve":True}).status_code==409
    assert beta.get("/api/trovoeda/balance").get_json()["trovoedas"]==0
    # A failed credit must leave the occurrence available for a safe retry.
    from unittest.mock import patch
    report_again=alpha.post(f"/api/leads/{own_id}/report",json={}).get_json()["report_id"]
    with patch("src.trovoeda.apply_delta_in_transaction",return_value={"ok":False}):
        assert host.post(f"/api/leads/{own_id}/resolve-report",json={"approve":True,"report_id":report_again}).status_code==503
    assert alpha.get("/api/trovoeda/balance").get_json()["trovoedas"]==40
    assert host.post(f"/api/leads/{own_id}/resolve-report",json={"approve":True,"report_id":report_again}).status_code==200
    assert alpha.get("/api/trovoeda/balance").get_json()["trovoedas"]==41
    assert alpha.post(f"/api/leads/{own_id}/report",json={}).status_code==200
    assert alpha.post(f"/api/leads/{own_id}/report",json={}).status_code==429
    # Registration + reset against disposable records, no SMTP network.
    from unittest.mock import patch
    signup=app.test_client()
    registration=signup.post("/register",data={"display_name":"Gamma teste","username":"green_gamma",
        "email":"gamma@example.invalid","whatsapp":"11976543218","password":"isolated-password-gamma","terms":"on"})
    assert registration.status_code==302,registration.status_code
    assert signup.get("/api/me").get_json()["username"]=="green_gamma"
    assert signup.get("/api/trovoeda/balance").get_json()["trovoedas"]==5
    signup.get("/logout")
    with patch("api.index._send_password_reset_email",return_value=True) as mail:
        forgotten=signup.post("/esqueci",data={"email":"gamma@example.invalid"})
        assert forgotten.status_code==200
        reset_url=mail.call_args.args[1]
    reset_path="/reset/"+reset_url.rsplit("/",1)[1]
    assert signup.get(reset_path).status_code==200
    changed=signup.post(reset_path,data={"password":"isolated-new-password-gamma","password2":"isolated-new-password-gamma"})
    assert changed.status_code==200 and "Senha atualizada".encode() in changed.data
    assert signup.post("/login",data={"username":"green_gamma","password":"isolated-new-password-gamma"}).status_code==302
    # Administrative actions use the same API as the newly restored forms.
    created=host.post("/api/users",json={"username":"green_delta","password":"isolated-password-delta","label":"Delta teste","monthly_quota":0})
    assert created.status_code==201,created.get_json()
    delta_id=created.get_json()["id"]
    assert users.get_user_by_id(delta_id)["monthly_quota"]==0
    assert host.post("/api/users",json={"username":"green_delta","password":"isolated-password-delta","monthly_quota":0}).status_code==409
    assert host.post("/api/users",json={"username":"green_other","password":"weak","monthly_quota":0}).status_code==400
    assert host.post(f"/api/users/{delta_id}",json={"label":"Delta editado","monthly_quota":7,"password":"isolated-updated-delta"}).status_code==200
    assert users.get_user_by_id(delta_id)["label"]=="Delta editado"
    delta=app.test_client()
    assert delta.post("/login",data={"username":"green_delta","password":"isolated-updated-delta"}).status_code==302
    assert host.post(f"/api/users/{delta_id}",json={"active":False}).status_code==200
    assert delta.get("/api/me").status_code==401
    assert host.post(f"/api/users/{delta_id}",json={"active":True}).status_code==200
    assert host.post(f"/api/users/{delta_id}/assign-plan",json={"plan_slug":"loki"}).status_code==200
    assert users.get_user_by_id(delta_id)["plan_slug"]=="loki"
    assert host.post(f"/api/users/{delta_id}/assign-plan",json={"plan_slug":None}).status_code==200
    assert users.get_user_by_id(delta_id)["monthly_quota"]==0
    assert host.post(f"/api/users/{delta_id}",json={"action":"reset-month"}).status_code==200
    assert host.post(f"/api/users/{delta_id}",json={"action":"delete"}).status_code==200
    assert users.get_user_by_id(delta_id) is None
    protected=host.post(f"/api/users/{first['id']}",json={"action":"delete"})
    assert protected.status_code==409,protected.get_json()
    assert users.get_user_by_id(first["id"]) is not None
    assert len(alpha.get("/api/leads").get_json())==2
    # Restore the administrative pool actions without changing account balances.
    free_ids=[]
    with psycopg2.connect(uri) as conn:
        with conn.cursor() as cur:
            for number in range(3):
                cur.execute("""INSERT INTO companies (place_id,name,city,state,niche,phone,website_status,lead_class,lead_score,scraped_at)
                    VALUES (%s,%s,'Salvador','BA','Odontologia','11987654321','sem_site','raio',88,NOW()::TEXT) RETURNING id""",
                    (f"pool-test-{number}",f"Pool teste {number}"))
                free_ids.append(cur.fetchone()[0])
    from api.index import _invalidate_cache
    _invalidate_cache()
    assert alpha.post("/api/leads/distribute",json={"lead_ids":[free_ids[0]],"user_id":first["id"]}).status_code==403
    quota_blocked=host.post("/api/leads/distribute",json={"lead_ids":[free_ids[0]],"user_id":first["id"],"respect_quota":True})
    assert quota_blocked.get_json()["distributed"]==0,quota_blocked.get_json()
    assert host.post(f"/api/users/{first['id']}",json={"monthly_quota":10}).status_code==200
    selected=host.post("/api/leads/distribute",json={"lead_ids":[free_ids[0]],"user_id":first["id"],"respect_quota":True})
    assert selected.get_json()["distributed"]==1,selected.get_json()
    assert alpha.get("/api/trovoeda/balance").get_json()["trovoedas"]==41
    assert host.post(f"/api/users/{second['id']}",json={"monthly_quota":10}).status_code==200
    automatic=host.post("/api/leads/distribute",json={"limit":1,"user_id":second["id"]})
    assert automatic.get_json()["distributed"]==1,automatic.get_json()
    assert host.post(f"/api/leads/{free_ids[1]}/assign",json={"assigned_to":None}).status_code==200
    assert alpha.post(f"/api/leads/{free_ids[2]}/delete",json={}).status_code==403
    assert host.post(f"/api/leads/{free_ids[2]}/delete",json={}).status_code==200
    # Delayed payment starts unpaid, then credits exactly once on async success.
    delayed={"id":"evt_delayed_pending","type":"checkout.session.completed","data":{"object":{
        "id":"cs_delayed_green","payment_status":"unpaid","client_reference_id":str(first["id"]),
        "metadata":{"package":"faisca","coins":"10"},"amount_total":1490,"currency":"brl"}}}
    def signed_event(body):
        raw=json.dumps(body)
        signature=hmac.new(os.environ["STRIPE_WEBHOOK_SECRET"].encode(),(stamp+"."+raw).encode(),hashlib.sha256).hexdigest()
        return alpha.post("/api/webhook/stripe",data=raw,headers={"Stripe-Signature":"t="+stamp+",v1="+signature,"Content-Type":"application/json"})
    assert signed_event(delayed).status_code==200
    assert alpha.get("/api/trovoeda/balance").get_json()["trovoedas"]==41
    delayed["id"]="evt_delayed_paid"
    delayed["type"]="checkout.session.async_payment_succeeded"
    delayed["data"]["object"]["payment_status"]="paid"
    assert signed_event(delayed).status_code==200
    assert alpha.get("/api/trovoeda/balance").get_json()["trovoedas"]==51
    exported=alpha.get("/api/export/csv?period=daily")
    assert exported.status_code==200
    assert b"Empresa Alpha" in exported.data and b"Empresa Beta" not in exported.data
    assert alpha.get("/api/reports/daily").get_json()["total"]==3
    logs=host.get("/api/audit?action=support_request&username=green_alpha").get_json()
    assert logs and all(row["action"]=="support_request" and row["username"]=="green_alpha" for row in logs)
    assert signed_event(delayed).status_code==200
    assert alpha.get("/api/trovoeda/balance").get_json()["trovoedas"]==51
    for route in ["/","/leads","/salvos","/quadro","/reports",f"/lead/{own_id}","/shop","/carteira","/pedidos","/pedir","/conta","/plano","/compras","/notificacoes","/ajuda","/checkout?package=faisca"]:
        response=alpha.get(route)
        assert response.status_code==200,(route,response.status_code)
    for route in ["/admin/leads","/admin/pedidos","/admin/ocorrencias","/users","/audit","/bot"]:
        response=host.get(route)
        assert response.status_code==200,(route,response.status_code)
    if os.getenv("GREEN_E2E_ARTIFACTS"):
        # Generated review artifacts contain only the disposable test records.
        artifacts=Path(os.environ["GREEN_E2E_ARTIFACTS"])
        artifacts.mkdir(parents=True,exist_ok=True)
        html=host.get("/users").get_data(as_text=True)
        (artifacts/"admin-clientes.html").write_text(html,encoding="utf-8")
        (artifacts/"admin-clientes-aberto.html").write_text(html.replace("<details>","<details open>",1),encoding="utf-8")
        pool=host.get("/admin/leads").get_data(as_text=True)
        (artifacts/"pool.html").write_text(pool,encoding="utf-8")
        (artifacts/"pool-aberto.html").write_text(pool.replace('<details class="live-inline">','<details class="live-inline" open>',1),encoding="utf-8")
        for name,client,route in [('lead',alpha,f'/lead/{own_id}'),('reports',alpha,'/reports?period=monthly'),('audit',host,'/audit'),('bot',host,'/bot')]:
            response=client.get(route)
            assert response.status_code==200,(route,response.status_code)
            (artifacts/f'{name}.html').write_text(response.get_data(as_text=True),encoding='utf-8')
    print("PASS: real isolated PostgreSQL; login/register/reset, ownership, favorites/status/notes, order reservation/cancel/refund/approve/deliver/reject, insufficient funds, checkout configuration error, forged return, signed offline Stripe webhook/replay/invalid signature, support persistence, report approve/reject/replay/failed-credit retry/weekly cap, admin create/edit/disable/reactivate/assign-remove plan/reset/delete/protected deletion and client/admin rendering.")
    print("Synthetic test database retained for diagnosis in "+directory+"; server stops on process exit.")
    if os.getenv("GREEN_E2E_SERVE") == "1":
        with psycopg2.connect(uri) as conn:
            with conn.cursor() as cur:
                for number in range(1,26):
                    cur.execute("""INSERT INTO companies (place_id,name,city,state,niche,phone,website_status,lead_class,lead_score,scraped_at)
                        VALUES (%s,%s,'Salvador','BA','Odontologia','11987654321','sem_site','raio',88,NOW()::TEXT)""",
                        (f"browser-{number}",f"Navegação {number:02d}"))
                cur.execute("""INSERT INTO companies (place_id,name,city,state,niche,phone,website_status,lead_class,lead_score,assigned_to,assigned_at,scraped_at)
                    VALUES ('browser-history','Histórico Alpha','Salvador','BA','Odontologia','11987654321','sem_site','raio',50,%s,(NOW()-INTERVAL '20 days')::TEXT,NOW()::TEXT)""",(first["id"],))
        _invalidate_cache()
        port=int(os.getenv("GREEN_E2E_PORT") or "8790")
        print(f"ISOLATED_BROWSER_TEST_READY http://127.0.0.1:{port}", flush=True)
        app.run(host="127.0.0.1",port=port,debug=False,use_reloader=False)


if __name__=="__main__":
    main()
