"""Durable background-job lifecycle and private storage tests."""

import shutil
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from flask import Flask

from background_service import (
    claim_job,
    complete_job,
    create_job,
    fail_stale_jobs,
    request_cancellation,
    reset_for_retry,
    update_progress,
)
from background_tasks import enqueue_job
from extensions import db
from job_storage import JobStorageError, get_bytes, put_bytes


class TestBackgroundJobs(unittest.TestCase):
    def setUp(self):
        self.temp_dir = (Path.cwd() / "instance" / f"test-job-storage-{uuid4()}").resolve()
        self.temp_dir.mkdir(parents=True)
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            JOB_STORAGE_BACKEND="local",
            JOB_STORAGE_DIR=str(self.temp_dir),
        )
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_job_claim_progress_completion_and_duplicate_delivery(self):
        job, reused = create_job(
            "report_generation", {"run_id": 4, "format": "pdf", "progress_total": 100}
        )
        self.assertFalse(reused)
        self.assertIsNotNone(claim_job(job.id))
        self.assertIsNone(claim_job(job.id), "A duplicate delivery must not execute twice")
        self.assertTrue(update_progress(job.id, 45, 100, "Generating"))
        completed = complete_job(job.id, {"result_url": "/jobs/1/download"})
        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.progress_current, 100)

    def test_idempotency_cancellation_and_retry(self):
        job, _ = create_job(
            "import_stage", {"progress_total": 20}, idempotency_key="a" * 64
        )
        same, reused = create_job(
            "import_stage", {"progress_total": 20}, idempotency_key="a" * 64
        )
        self.assertTrue(reused)
        self.assertEqual(same.id, job.id)
        cancelled = request_cancellation(job.id)
        self.assertEqual(cancelled.status, "cancelled")
        old_task_id = cancelled.task_id
        retried = reset_for_retry(job.id)
        self.assertEqual(retried.status, "queued")
        self.assertNotEqual(retried.task_id, old_task_id)

    def test_abandoned_running_job_can_be_recovered_for_review(self):
        job, _ = create_job("report_generation", {"progress_total": 100})
        running = claim_job(job.id)
        running.started_at = datetime.now(timezone.utc) - timedelta(hours=2)
        db.session.commit()
        self.assertEqual(
            fail_stale_jobs(datetime.now(timezone.utc) - timedelta(hours=1)), 1
        )
        self.assertEqual(db.session.get(type(job), job.id).status, "failed")

    def test_private_local_storage_blocks_path_traversal(self):
        put_bytes("reports/job-1/report.pdf", b"report")
        self.assertEqual(get_bytes("reports/job-1/report.pdf"), b"report")
        self.assertTrue((self.temp_dir / "reports/job-1/report.pdf").is_file())
        with self.assertRaises(JobStorageError):
            put_bytes("../outside.txt", b"unsafe")

    def test_enqueue_uses_database_task_id_and_known_task_name(self):
        job, _ = create_job(
            "seating_optimization", {"form": {}, "progress_total": 100}
        )
        with patch("background_tasks.celery_app.send_task") as send_task:
            enqueue_job(job)
        send_task.assert_called_once_with(
            "seatplan.optimize", args=[job.id], task_id=job.task_id
        )


if __name__ == "__main__":
    unittest.main()
