"""Security and payment boundaries; isolated fixtures, no external services."""
import os
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["DATABASE_URL"] = ""
os.environ.setdefault("FLASK_SECRET_KEY", "isolated-green-tests-only")

import unittest
from unittest.mock import Mock, patch

from api import index as web


class IntegrationGuardTests(unittest.TestCase):
    def setUp(self):
        web.app.config.update(TESTING=True)
        self.client = web.app.test_client()

    def login_client(self):
        with self.client.session_transaction() as session:
            session.update(logged_in=True, user_id=7, username="cliente", role="client")

    def test_public_landing_and_catalog_do_not_run_schema_or_seed_writes(self):
        from src import trovoeda
        cursor=Mock()
        cursor.fetchall.return_value=[{"id":1,"slug":"faisca","name":"Faísca","coins":10,
            "price_cents":1490,"active":1,"sort_order":1,"daily_cap":None}]
        connection=Mock()
        connection.cursor.return_value=cursor
        with patch.object(trovoeda,'_DATABASE_URL','isolated'), \
             patch.object(trovoeda,'_connect',return_value=connection), \
             patch.object(trovoeda,'ensure_schema',side_effect=AssertionError('read path attempted DDL')) as schema, \
             patch.object(trovoeda,'public_stats',return_value={'leads_total':321,'cities':2,'niches':3}):
            response=self.client.get('/landing')
        self.assertEqual(response.status_code,200)
        self.assertIn('Faísca'.encode(),response.data)
        self.assertIn(b'321',response.data)
        schema.assert_not_called()
        self.assertEqual(cursor.execute.call_count,1)
        self.assertTrue(cursor.execute.call_args.args[0].strip().startswith('SELECT'))
        connection.commit.assert_not_called()
        connection.close.assert_called_once()

    def test_account_outage_is_not_a_zero_balance(self):
        self.login_client()
        with patch("src.users.get_user_by_id", side_effect=RuntimeError("isolated outage")):
            response = self.client.get("/api/me")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("trovoedas", response.get_json())

    def test_disabled_account_cannot_reuse_existing_session(self):
        self.login_client()
        with patch("src.users.get_user_by_id", return_value={"id":7,"active":False}):
            self.assertEqual(self.client.get("/api/me").status_code, 401)
        with self.client.session_transaction() as session:
            self.assertFalse(session.get("logged_in"))

    def test_lead_outage_is_not_an_empty_portfolio(self):
        self.login_client()
        web._invalidate_cache()
        with patch.object(web, "DATABASE_URL", "postgres://fixture"), \
             patch("src.users.ensure_schema"), patch.object(web, "_ensure_leads_extra_schema"), \
             patch.object(web, "get_db", side_effect=RuntimeError("isolated outage")):
            response = self.client.get("/api/leads")
        self.assertEqual(response.status_code, 503)
        self.assertNotEqual(response.data, b"[]\n")

    def test_all_admin_actions_are_blocked_while_impersonating(self):
        with self.client.session_transaction() as session:
            session.update(logged_in=True, user_id=7, username="cliente", role="client",
                           impersonating=True, real_user_id=1, real_username="patrao", real_role="admin")
        for path, method in [("/api/users", "get"), ("/api/audit", "get"),
                             ("/api/orders/1/approve", "post"),
                             ("/api/leads/1/resolve-report", "post")]:
            with self.subTest(path=path):
                self.assertEqual(getattr(self.client, method)(path).status_code, 403)
        with patch("src.audit.log_action"):
            response = self.client.post("/api/impersonate/stop", json={})
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            self.assertFalse(session.get("impersonating"))
            self.assertEqual(session["username"], "patrao")

    def test_failed_stripe_credit_remains_retryable(self):
        event = Mock()
        event.to_dict.return_value = {
            "id": "evt_fixture", "type": "checkout.session.completed", "data": {"object": {
                "id": "cs_fixture", "payment_status": "paid", "client_reference_id": "7",
                "metadata": {"package": "faisca", "coins": "10"},
                "amount_total": 1490, "currency": "brl"}}}
        with patch.dict(os.environ, {"STRIPE_WEBHOOK_SECRET": "whsec_fixture"}), \
             patch("stripe.Webhook.construct_event", return_value=event), \
             patch("src.trovoeda.credit_stripe_purchase", return_value={"ok": False, "error": "erro interno"}):
            response = self.client.post("/api/webhook/stripe", data=b"fixture")
        self.assertEqual(response.status_code, 500)
        self.assertIn(b"Payment credit failed", response.data)

    def test_checkout_uses_catalog_not_stale_price_id_or_client_amount(self):
        self.login_client()
        package={"slug":"faisca","name":"Faísca","price_cents":1490,"coins":10,
                 "stripe_price_id":"price_historical_wrong_amount","daily_cap":None}
        checkout=Mock(url="https://checkout.stripe.com/c/pay/fixture",id="cs_fixture")
        with patch("src.trovoeda.ensure_schema"), patch("src.trovoeda.list_packages",return_value=[package]), \
             patch.dict(os.environ,{"STRIPE_API_KEY":"sk_test_fixture"}), \
             patch("stripe.checkout.Session.create",return_value=checkout) as create:
            response=self.client.post("/api/trovoeda/checkout",json={"package":"faisca","price_cents":1,"coins":9999})
        self.assertEqual(response.status_code,200)
        sent=create.call_args.kwargs
        self.assertEqual(sent["line_items"][0]["price_data"]["unit_amount"],1490)
        self.assertEqual(sent["line_items"][0]["price_data"]["currency"],"brl")
        self.assertNotIn("price",sent["line_items"][0])
        self.assertEqual(sent["metadata"]["coins"],"10")
        self.assertEqual(sent["client_reference_id"],"7")

    def test_delayed_payment_waits_for_successful_confirmation(self):
        event=Mock()
        body={"id":"evt_async_fixture","type":"checkout.session.completed","data":{"object":{
            "id":"cs_async_fixture","payment_status":"unpaid","client_reference_id":"7",
            "metadata":{"package":"faisca","coins":"10"},"amount_total":1490,"currency":"brl"}}}
        event.to_dict.side_effect=lambda:body
        with patch.dict(os.environ,{"STRIPE_WEBHOOK_SECRET":"whsec_fixture"}), \
             patch("stripe.Webhook.construct_event",return_value=event), \
             patch("src.trovoeda.credit_stripe_purchase",return_value={"ok":True}) as credit:
            self.assertEqual(self.client.post("/api/webhook/stripe",data=b"fixture").status_code,200)
            credit.assert_not_called()
            body["type"]="checkout.session.async_payment_succeeded"
            body["data"]["object"]["payment_status"]="paid"
            self.assertEqual(self.client.post("/api/webhook/stripe",data=b"fixture").status_code,200)
            credit.assert_called_once()

    def test_monthly_plan_cannot_be_purchased_as_one_time_recharge(self):
        self.login_client()
        with patch("src.trovoeda.ensure_schema"), patch("src.trovoeda.list_packages",return_value=[
            {"slug":"thor","daily_cap":30,"price_cents":58900,"coins":900}]), \
             patch("stripe.checkout.Session.create") as create:
            self.assertEqual(self.client.post("/api/trovoeda/checkout",json={"package":"thor"}).status_code,409)
        create.assert_not_called()

    def test_invalid_distribution_never_falls_back_to_all_customers(self):
        with self.client.session_transaction() as session:
            session.update(logged_in=True,user_id=1,username="patrao",role="admin")
        with patch("src.users.distribute_free_leads") as automatic, patch("src.users.distribute_selected_leads") as selected:
            for payload in [{"lead_ids":[1]}, {"lead_ids":[-1],"user_id":7}, {"limit":"invalid"}, {"limit":0}, {"user_id":"invalid"}]:
                with self.subTest(payload=payload):
                    self.assertEqual(self.client.post("/api/leads/distribute",json=payload).status_code,400)
        automatic.assert_not_called()
        selected.assert_not_called()

    def test_distribution_failure_is_not_success_or_raw_database_error(self):
        with self.client.session_transaction() as session:
            session.update(logged_in=True,user_id=1,username="patrao",role="admin")
        with patch("src.users.distribute_free_leads",return_value={"ok":False,"message":"private database failure"}):
            response=self.client.post("/api/leads/distribute",json={"limit":1})
        self.assertEqual(response.status_code,503)
        self.assertNotIn(b"private database",response.data)


if __name__ == "__main__":
    unittest.main()
