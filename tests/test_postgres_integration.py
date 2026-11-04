"""PostgreSQL-only migration and transaction smoke checks.

The test is skipped during the fast local suite and enabled by the dedicated CI
PostgreSQL job after Alembic migrations have been applied.
"""

import os
import unittest
from uuid import uuid4

from sqlalchemy import inspect, select, text


POSTGRES_TEST_ENABLED = os.environ.get("RUN_POSTGRES_INTEGRATION") == "1"


@unittest.skipUnless(POSTGRES_TEST_ENABLED, "PostgreSQL integration environment is not enabled")
class TestPostgresIntegration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from app import app
        from extensions import db

        cls.app = app
        cls.db = db
        cls.context = app.app_context()
        cls.context.push()

    @classmethod
    def tearDownClass(cls):
        cls.db.session.remove()
        cls.context.pop()

    def test_migration_head_and_expected_tables_exist(self):
        self.assertEqual(self.db.engine.dialect.name, "postgresql")
        tables = set(inspect(self.db.engine).get_table_names())
        self.assertTrue({
            "alembic_version",
            "datasets",
            "students",
            "exam_registrations",
            "seating_runs",
            "seat_allocations",
        }.issubset(tables))
        version = self.db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        self.assertEqual(version, "08f8080be851")

    def test_postgresql_transaction_rollback_preserves_database_state(self):
        from models import Dataset

        marker = f"postgres-transaction-{uuid4()}"
        with self.db.engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                Dataset.__table__.insert().values(
                    name=marker,
                    version="rollback-check",
                    source_type="real",
                    status="draft",
                )
            )
            inside_rows = connection.execute(
                select(Dataset.id).where(Dataset.name == marker)
            ).all()
            self.assertEqual(len(inside_rows), 1)
            transaction.rollback()

        with self.db.engine.connect() as connection:
            outside_rows = connection.execute(
                select(Dataset.id).where(Dataset.name == marker)
            ).all()
        self.assertEqual(outside_rows, [])


if __name__ == "__main__":
    unittest.main()
