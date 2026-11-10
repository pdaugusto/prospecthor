"""Presentation integration checks; fixtures only, never production services."""
import os
os.environ["DATABASE_URL"] = ""
os.environ.setdefault("FLASK_SECRET_KEY", "isolated-green-tests-only")
import unittest
from contextlib import ExitStack
from unittest.mock import patch
from flask import jsonify
from api import index as web
from api.green_ui import safe_url, phone_url, metrics


class GreenPresentationTests(unittest.TestCase):
    def setUp(self):
        web.app.config.update(TESTING=True)
        self.client = web.app.test_client()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.me = {"id":7,"username":"cliente","label":"Cliente real","is_admin":False,
                   "plan_slug":"thor","trovoedas":24,"trovoedas_infinite":False}
        self.leads = [{"id":41,"name":"Empresa <script>alert(1)</script>","niche":"Odonto","city":"Salvador","state":"BA",
                      "phone":"11987654321","lead_score":88,"contact_status":"novo","assigned_to":7,
                      "is_favorite":True,"lead_problems":'["Sem site"]',"lead_services":'["Site"]'}]
        self.packages=[{"slug":"thor","name":"Thor","coins":900,"price_cents":58900,"price_brl":"589,00","per_lead":"0,65","daily_cap":30,"duration_days":30},
                       {"slug":"faisca","name":"Faísca","coins":10,"price_cents":1490,"price_brl":"14,90","per_lead":"1,49","daily_cap":None}]
        self.order={"id":51,"user_id":7,"quantity":10,"delivered":0,"reserved":10,"status":"pending","created_at":"2026-11-10T12:00:00"}
        for endpoint, value in {
            "api_me":self.me, "api_orders_list":{"orders":[self.order]}, "api_trovoeda_ledger":[],
            "api_users_list":[], "api_audit":[], "api_bot_status":{}, "api_bot_plan_get":{}
        }.items():
            self.stack.enter_context(patch.dict(web.app.view_functions,{endpoint:lambda value=value:jsonify(value)}))
        self.stack.enter_context(patch.object(web,"get_all_leads",return_value=self.leads))
        self.stack.enter_context(patch.object(web,"get_lead_by_id",return_value=self.leads[0]))
        self.stack.enter_context(patch.object(web,"_packages_for_view",return_value=self.packages))
        self.stack.enter_context(patch("src.orders.get_order",return_value=self.order))
        self.stack.enter_context(patch("src.users.get_user_by_id",return_value={"email":"client@example.invalid"}))
        self.stack.enter_context(patch("src.bot_plan.load_catalog",return_value={"cities":[],"niches":[]}))
        self.stack.enter_context(patch("src.trovoeda.ensure_schema"))
        self.stack.enter_context(patch("src.trovoeda.public_stats",return_value={"leads_total":31,"cities":2,"niches":3}))

    def login(self,admin=False):
        self.me["is_admin"]=admin
        with self.client.session_transaction() as sess:
            sess.update(logged_in=True,user_id=1 if admin else 7,username="patrao" if admin else "cliente",role="admin" if admin else "client")

    def test_public_pages_render(self):
        for path in ["/","/landing","/login","/register","/esqueci","/termos","/privacidade"]:
            with self.subTest(path=path):
                response=self.client.get(path)
                self.assertEqual(response.status_code,200)
                self.assertNotIn(b"auth.js",response.data)

    def test_every_client_surface_renders_real_values(self):
        self.login()
        for path in ["/","/leads","/salvos","/quadro","/reports","/lead/41","/shop","/carteira","/compras",
                     "/pedir","/pedidos","/pedido/51","/checkout?package=faisca","/pagamento","/plano","/conta","/ajuda","/notificacoes"]:
            with self.subTest(path=path):
                response=self.client.get(path)
                self.assertEqual(response.status_code,200)
                self.assertNotIn(b"Lucas Silva",response.data)
                self.assertNotIn(b"complemento.js",response.data)
        self.assertIn(b"Cliente real",self.client.get("/").data)
        self.assertIn(b"&lt;script&gt;",self.client.get("/leads").data)

    def test_private_pages_require_session(self):
        for path in ["/leads","/salvos","/pedir","/pedido/51","/checkout?package=faisca","/ajuda","/admin/leads"]:
            self.assertEqual(self.client.get(path).status_code,302)

    def test_client_cannot_open_admin_surfaces(self):
        self.login()
        for path in ["/users","/audit","/bot","/admin/leads","/admin/pedidos","/admin/ocorrencias"]:
            self.assertEqual(self.client.get(path).status_code,403)

    def test_admin_surfaces_render(self):
        self.login(True)
        for path in ["/users","/audit","/bot","/admin/leads","/admin/pedidos","/admin/ocorrencias"]:
            with self.subTest(path=path): self.assertEqual(self.client.get(path).status_code,200)

    def test_admin_client_forms_use_live_endpoints_and_escape_values(self):
        self.login(True)
        with patch.dict(web.app.view_functions, {"api_users_list":lambda:jsonify([
            {"id":8,"username":"cliente","role":"client","label":"Nome <script>","active":True,"monthly_quota":0}
        ])}):
            html=self.client.get("/users").get_data(as_text=True)
        self.assertIn('data-live-form="user-create"',html)
        self.assertIn('data-live-form="user-edit"',html)
        self.assertIn('data-user-id="8"',html)
        self.assertIn('value="Nome &lt;script&gt;"',html)
        self.assertIn('"action":"reset-month"',html)
        self.assertNotIn('type="password" value=',html)

    def test_operation_is_read_only_and_displays_recorded_data(self):
        self.login(True)
        with patch.dict(web.app.view_functions, {
            "api_bot_status":lambda:jsonify({"status":"rodando","session_leads_count":12,"mission_target":20,
                "niche_counts":{"Odonto":12},"logs":[{"level":"INFO","message":"Atividade <script>","created_at":"2026-11-10T12:00:00"}]}),
            "api_bot_plan_get":lambda:jsonify({"target_leads":20,"city_ids":["salvador"],"niche_ids":["odonto"],
                "catalog":{"cities":[{"id":"salvador","nome":"Salvador","estado":"BA"}],"niches":[{"id":"odonto","label":"Odontologia"}]}})
        }):
            html=self.client.get('/bot').get_data(as_text=True)
        self.assertIn('Em execução',html)
        self.assertIn('Salvador / BA',html)
        self.assertIn('Odontologia',html)
        self.assertIn('Atividade &lt;script&gt;',html)
        self.assertNotIn('/api/bot-status/stop',html)
        self.assertNotIn('data-live-form=',html)
        self.assertIn('não confirma que o processo continua ativo',html)

    def test_foreign_order_hidden(self):
        self.login()
        self.order["user_id"]=99
        self.assertEqual(self.client.get("/pedido/51").status_code,404)

    def test_private_html_is_not_cached(self):
        self.login()
        response=self.client.get("/leads")
        self.assertIn("no-store",response.headers["Cache-Control"])
        self.assertIn("Cookie",response.headers.get("Vary",""))

    def test_checkout_requires_active_credit_package(self):
        self.login()
        self.assertEqual(self.client.get("/checkout?package=thor").status_code,404)
        self.assertEqual(self.client.get("/checkout?package=unknown").status_code,404)

    def test_error_page_is_not_fixture_fallback(self):
        self.login()
        with patch.object(web,"get_all_leads",side_effect=RuntimeError("isolated outage")):
            response=self.client.get("/leads")
        self.assertEqual(response.status_code,503)
        self.assertIn("Não foi possível".encode(),response.data)

    def test_helpers_reject_script_urls(self):
        self.assertEqual(safe_url("javascript:alert(1)"),"")
        self.assertEqual(safe_url("https://["),"")
        self.assertEqual(phone_url("(11) 98765-4321"),"tel:+5511987654321")
        self.assertEqual(metrics(self.leads)["priority"],1)

    def test_auth_posts_are_not_simulated(self):
        with patch("src.users.authenticate",return_value=None):
            response=self.client.post("/login",data={"username":"not_real","password":"wrong"})
        self.assertEqual(response.status_code,200)
        self.assertIn("Usuário ou senha incorretos".encode(),response.data)
        self.assertNotIn(b"cliente/index.html",response.data)
        with self.client.session_transaction() as sess: self.assertFalse(sess.get("logged_in"))

    def test_support_requires_login_and_same_origin(self):
        self.assertEqual(self.client.post("/api/support",json={}).status_code,401)
        self.login()
        self.assertEqual(self.client.post("/api/support",json={},headers={"Origin":"https://evil.invalid"}).status_code,403)
        self.assertEqual(self.client.post("/api/support",json={"subject":"Help","message":"tiny"}).status_code,400)


if __name__=="__main__":
    unittest.main()
