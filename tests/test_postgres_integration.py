"""PostgreSQL-only migration and transaction smoke checks.

The test is skipped during the fast local suite and enabled by the dedicated CI
PostgreSQL job after Alembic migrations have been applied.
"""

import os
import unittest
from datetime import date, time
from io import BytesIO
from uuid import uuid4

from sqlalchemy import inspect, select, text
from werkzeug.datastructures import FileStorage


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
            "institutions",
            "departments",
            "academic_years",
            "student_academic_records",
            "import_staging_students",
            "import_row_errors",
            "datasets",
            "students",
            "exam_registrations",
            "seating_runs",
            "seat_allocations",
        }.issubset(tables))
        version = self.db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        self.assertEqual(version, "c2d4e6f8a913")

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

    def test_postgresql_copy_stages_valid_and_invalid_rows(self):
        from data_service import stage_student_import
        from models import ImportRowError, ImportStagingStudent

        marker = uuid4().hex
        upload = FileStorage(
            stream=BytesIO(
                b"ID,Class,Subject,Special_Needs\n"
                b"PG-001,CSE-A,Mathematics,N\n"
                b"PG-002,CSE-B,Physics,INVALID\n"
            ),
            filename="postgres-copy.csv",
            content_type="text/csv",
        )
        staged, reused = stage_student_import(
            upload,
            dataset_name=f"PostgreSQL COPY {marker}",
            dataset_version="v1",
            exam_date=date(2026, 12, 1),
            start_time=time(9, 0),
            end_time=time(12, 0),
            session_name="Morning",
            academic_year="2026-27",
            chunk_size=100,
        )
        self.assertFalse(reused)
        self.assertEqual((staged.valid_rows, staged.invalid_rows), (1, 1))
        self.assertEqual(
            self.db.session.scalar(
                select(ImportStagingStudent).where(
                    ImportStagingStudent.data_import_id == staged.id
                )
            ).student_code,
            "PG-001",
        )
        self.assertEqual(
            self.db.session.scalar(
                select(ImportRowError).where(ImportRowError.data_import_id == staged.id)
            ).row_number,
            3,
        )
        self.db.session.delete(staged)
        self.db.session.commit()


if __name__ == "__main__":
    unittest.main()
