import os
import unittest

os.environ.pop("VERCEL", None)
os.environ.pop("VERCEL_ENV", None)
os.environ.pop("PRODUCTION", None)
os.environ.pop("FLASK_SECRET_KEY", None)

from api.index import app
from src.users import _hash_password, verify_password


class SecurityTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def test_private_api_requires_authentication(self):
        self.assertEqual(self.client.get("/api/leads").status_code, 401)

    def test_cross_site_write_is_rejected(self):
        response = self.client.post(
            "/login",
            data={"username": "x", "password": "y"},
            headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(response.status_code, 403)

    def test_security_headers_are_present(self):
        response = self.client.get("/login")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("geolocation=()", response.headers["Permissions-Policy"])
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    def test_password_hash_is_salted_and_verifiable(self):
        first = _hash_password("uma-senha-forte")
        second = _hash_password("uma-senha-forte")
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password("uma-senha-forte", first))
        self.assertFalse(verify_password("senha-errada", first))


if __name__ == "__main__":
    unittest.main()
