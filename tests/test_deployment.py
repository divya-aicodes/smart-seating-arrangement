import os
import json
import logging
import subprocess
import sys
import unittest

from app import _parse_allowed_hosts, _parse_cors_origins, app
from config import JsonLogFormatter, load_app_config


class TestDeploymentConfiguration(unittest.TestCase):

    def test_origin_and_host_allowlists_reject_unsafe_patterns(self):
        self.assertEqual(
            _parse_cors_origins("https://app.example.edu, http://localhost:5173/"),
            {"https://app.example.edu", "http://localhost:5173"},
        )
        self.assertEqual(
            _parse_allowed_hosts("APP.EXAMPLE.EDU,localhost"),
            {"app.example.edu", "localhost"},
        )
        with self.assertRaises(RuntimeError):
            _parse_cors_origins("https://*.example.edu")
        with self.assertRaises(RuntimeError):
            _parse_cors_origins("https://example.edu/api")
        with self.assertRaises(RuntimeError):
            _parse_allowed_hosts("*")
        with self.assertRaises(RuntimeError):
            _parse_allowed_hosts("https://app.example.edu")

    def test_security_headers_are_applied(self):
        client = app.test_client()
        response = client.get("/api/v1/openapi.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("camera=()", response.headers["Permissions-Policy"])
        self.assertRegex(response.headers["X-Request-ID"], r"^[a-f0-9]{32}$")

    def test_valid_request_id_is_preserved_and_invalid_value_is_replaced(self):
        client = app.test_client()
        supplied = client.get(
            "/api/v1/openapi.json", headers={"X-Request-ID": "seatplan-check-123"}
        )
        self.assertEqual(supplied.headers["X-Request-ID"], "seatplan-check-123")

        replaced = client.get(
            "/api/v1/openapi.json", headers={"X-Request-ID": "unsafe request id"}
        )
        self.assertNotEqual(replaced.headers["X-Request-ID"], "unsafe request id")
        self.assertRegex(replaced.headers["X-Request-ID"], r"^[a-f0-9]{32}$")

    def test_centralized_config_normalizes_postgresql_and_pool_settings(self):
        config = load_app_config({
            "APP_ENV": "production",
            "SECRET_KEY": "production-test-secret-key-with-more-than-32-characters",
            "DATABASE_URL": "postgresql://user:password@database/seatplan",
            "SESSION_COOKIE_SECURE": "true",
            "ALLOWED_HOSTS": "app.example.edu",
        })
        self.assertEqual(
            config["SQLALCHEMY_DATABASE_URI"],
            "postgresql+psycopg://user:password@database/seatplan",
        )
        self.assertEqual(config["SQLALCHEMY_ENGINE_OPTIONS"]["pool_size"], 5)
        self.assertTrue(config["IS_PRODUCTION"])

    def test_structured_log_formatter_emits_json(self):
        record = logging.LogRecord(
            "seatplan.test", logging.INFO, __file__, 1, "configuration ready", (), None
        )
        record.request_id = "seatplan-check-123"
        payload = json.loads(JsonLogFormatter().format(record))
        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(payload["logger"], "seatplan.test")
        self.assertEqual(payload["message"], "configuration ready")
        self.assertEqual(payload["request_id"], "seatplan-check-123")

    def _production_import(self, **overrides):
        environment = os.environ.copy()
        environment.update({
            "APP_ENV": "production",
            "SECRET_KEY": "production-test-secret-key-with-more-than-32-characters",
            "DATABASE_URL": "postgresql+psycopg://user:password@localhost/database",
            "SESSION_COOKIE_SECURE": "true",
            "SESSION_COOKIE_SAMESITE": "Lax",
            "ALLOWED_HOSTS": "app.example.edu",
            "CORS_ALLOWED_ORIGINS": "https://app.example.edu",
            "CORS_ALLOW_CREDENTIALS": "false",
            "TRUST_PROXY_HEADERS": "false",
        })
        environment.update(overrides)
        return subprocess.run(
            [sys.executable, "-c", "import app; print(app.app.config['SESSION_COOKIE_SECURE'])"],
            cwd=os.getcwd(),
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def test_valid_production_configuration_imports_without_database_connection(self):
        result = self._production_import()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "True")

    def test_production_rejects_placeholder_secret_and_sqlite(self):
        placeholder = self._production_import(SECRET_KEY="[REDACTED_SECRET]")
        self.assertNotEqual(placeholder.returncode, 0)
        self.assertIn("non-placeholder", placeholder.stderr)

        sqlite = self._production_import(DATABASE_URL="sqlite:///unsafe-production.db")
        self.assertNotEqual(sqlite.returncode, 0)
        self.assertIn("must use PostgreSQL", sqlite.stderr)


if __name__ == "__main__":
    unittest.main()
