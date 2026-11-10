import unittest
from unittest.mock import patch

from src import orders, trovoeda


class FakeCursor:
    def __init__(self, *, duplicate=False, order=None):
        self.duplicate = duplicate
        self.order = order
        self.rowcount = 1
        self.executed = []
        self._last_sql = ""

    def execute(self, sql, params=None):
        self._last_sql = " ".join(sql.split())
        self.executed.append((self._last_sql, params))
        if "INSERT INTO stripe_payments" in self._last_sql:
            self.rowcount = 0 if self.duplicate else 1
        elif "UPDATE lead_orders" in self._last_sql:
            self.rowcount = 1

    def fetchone(self):
        if "FROM trovoeda_packages" in self._last_sql:
            return (10, 1490)
        if "FROM app_users" in self._last_sql:
            return (3,)
        if "INSERT INTO lead_orders" in self._last_sql:
            return {"id": 41, "user_id": 7, "quantity": 4, "delivered": 0, "reserved": 4}
        if "FROM lead_orders" in self._last_sql:
            return self.order
        return None

    def close(self):
        pass


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def cursor(self, *args, **kwargs):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1


class FinancialFlowTests(unittest.TestCase):
    def test_reservation_accepts_real_dict_cursor_balance(self):
        class DictCursor(FakeCursor):
            def fetchone(self):
                return {"balance": 10}
        cursor = DictCursor()
        result = trovoeda.apply_delta_in_transaction(cursor, 7, -4, reason="reserve")
        self.assertTrue(result["ok"])
        self.assertEqual(result["balance"], 6)

    def test_stripe_purchase_is_atomic_and_catalog_validated(self):
        cursor = FakeCursor()
        conn = FakeConnection(cursor)
        with patch.object(trovoeda, "ensure_schema"), patch.object(trovoeda, "_connect", return_value=conn):
            result = trovoeda.credit_stripe_purchase(
                user_id=7,
                coins=10,
                package_slug="faisca",
                session_id="cs_paid_1",
                event_id="evt_1",
                amount_total=1490,
                currency="brl",
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["balance"], 13)
        self.assertEqual(conn.commits, 1)
        self.assertTrue(any("INSERT INTO stripe_payments" in sql for sql, _ in cursor.executed))
        self.assertTrue(any("INSERT INTO trovoeda_ledger" in sql for sql, _ in cursor.executed))

    def test_replayed_stripe_session_does_not_credit_again(self):
        cursor = FakeCursor(duplicate=True)
        conn = FakeConnection(cursor)
        with patch.object(trovoeda, "ensure_schema"), patch.object(trovoeda, "_connect", return_value=conn), patch.object(trovoeda, "get_balance", return_value=13):
            result = trovoeda.credit_stripe_purchase(
                user_id=7, coins=10, package_slug="faisca", session_id="cs_paid_1",
                event_id="evt_2", amount_total=1490, currency="brl",
            )
        self.assertTrue(result["duplicate"])
        self.assertEqual(result["delta"], 0)
        self.assertFalse(any("UPDATE app_users" in sql for sql, _ in cursor.executed))

    def test_order_and_reservation_share_one_transaction(self):
        cursor = FakeCursor()
        conn = FakeConnection(cursor)
        seen = []

        def reserve(cur, user_id, delta, **kwargs):
            seen.append((cur, user_id, delta))
            return {"ok": True, "balance": 6, "delta": delta}

        with patch.object(orders, "_DATABASE_URL", "postgres://test"), patch.object(orders, "ensure_schema"), patch.object(orders, "_connect", return_value=conn), patch.object(trovoeda, "get_balance", return_value=10), patch.object(trovoeda, "apply_delta_in_transaction", side_effect=reserve), patch.object(orders, "get_order", return_value={"id": 41}):
            result = orders.create_order(7, 4)
        self.assertTrue(result["ok"])
        self.assertEqual(conn.commits, 1)
        self.assertEqual(seen, [(cursor, 7, -4)])

    def test_cancel_locks_order_before_refund(self):
        locked = {"id": 41, "user_id": 7, "status": "pending", "reserved": 4, "delivered": 0}
        cursor = FakeCursor(order=locked)
        conn = FakeConnection(cursor)
        seen = []

        def release(cur, user_id, delta, **kwargs):
            seen.append((cur, user_id, delta))
            return {"ok": True, "balance": 10, "delta": delta}

        with patch.object(orders, "_DATABASE_URL", "postgres://test"), patch.object(orders, "ensure_schema"), patch.object(orders, "_connect", return_value=conn), patch.object(trovoeda, "apply_delta_in_transaction", side_effect=release), patch.object(orders, "get_order", return_value=locked):
            result = orders.cancel_order(41, 7)
        self.assertTrue(result["ok"])
        self.assertEqual(seen, [(cursor, 7, 4)])
        self.assertEqual(conn.commits, 1)
        self.assertIn("FOR UPDATE", cursor.executed[0][0])


if __name__ == "__main__":
    unittest.main()
