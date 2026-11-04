import unittest
from unittest.mock import patch

from benchmarks.common import qualifying_import_claims
from benchmarks.import_benchmark import parse_sizes, require_disposable_postgres
from benchmarks.optimization_benchmark import (
    hard_violation_count,
    parse_sizes as parse_optimization_sizes,
    room_configuration,
)


class BenchmarkGuardrailTests(unittest.TestCase):
    def test_import_benchmark_requires_explicit_database(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "BENCHMARK_DATABASE_URL"):
                require_disposable_postgres()

    def test_import_benchmark_rejects_sqlite(self):
        environment = {
            "BENCHMARK_DATABASE_URL": "sqlite:///benchmark.db",
            "BENCHMARK_ALLOW_WRITES": "1",
        }
        with patch.dict("os.environ", environment, clear=True):
            with self.assertRaisesRegex(RuntimeError, "require PostgreSQL"):
                require_disposable_postgres()

    def test_size_parsers_enforce_documented_bounds(self):
        self.assertEqual(parse_sizes("1000,100000"), [1_000, 100_000])
        self.assertEqual(parse_optimization_sizes("25,25000"), [25, 25_000])
        with self.assertRaises(Exception):
            parse_sizes("999")
        with self.assertRaises(Exception):
            parse_optimization_sizes("25001")

    def test_optimization_rooms_are_capacity_bounded_and_not_grossly_oversized(self):
        small = room_configuration(24)
        self.assertEqual(len(small), 1)
        self.assertGreaterEqual(small[0]["rows"] * 15 * 4, 24)
        self.assertLessEqual(small[0]["rows"] * 15 * 4, 24 + 59)
        self.assertEqual(len(room_configuration(1_201)), 2)


class BenchmarkClaimTests(unittest.TestCase):
    @staticmethod
    def measurement(rows, *, dialect="postgresql", committed=True):
        return {
            "suite": "import",
            "requested_rows": rows,
            "database": {"dialect": dialect},
            "staging": {
                "status": "validated",
                "valid_rows": rows,
                "invalid_rows": 0,
            },
            "commit": {
                "status": "completed" if committed else "failed",
                "committed_rows": rows,
            },
        }

    def test_claims_require_exact_successful_postgres_measurements(self):
        claims = qualifying_import_claims([
            self.measurement(100_000),
            self.measurement(500_000, committed=False),
            self.measurement(500_000, dialect="sqlite"),
        ])
        self.assertTrue(claims["postgresql_import_100000"])
        self.assertFalse(claims["postgresql_import_500000"])

    def test_hard_violation_counter_excludes_soft_constraints(self):
        certificate = {
            "propositions": [
                {"type": "HARD CONSTRAINT", "violation_count": 2},
                {"type": "SOFT CONSTRAINT (RELAXABLE)", "violation_count": 8},
            ]
        }
        self.assertEqual(hard_violation_count(certificate), 2)
