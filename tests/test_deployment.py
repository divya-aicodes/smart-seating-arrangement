import os
import subprocess
import sys
import unittest

from app import _parse_allowed_hosts, _parse_cors_origins, app


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
