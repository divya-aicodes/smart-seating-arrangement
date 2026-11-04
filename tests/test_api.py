import os
import re
import unittest
from datetime import date, datetime, timezone

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app import app
from background_service import create_job
from data_service import generate_synthetic_dataset
from extensions import db
from models import (
    AuditLog,
    Dataset,
    Exam,
    ExamRegistration,
    ExamSession,
    Seat,
    SeatAllocation,
    SeatingRun,
    Student,
    Subject,
)


class TestRestApi(unittest.TestCase):

    def setUp(self):
        self.app = app
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()
        self.app.config["WTF_CSRF_ENABLED"] = False

    def csrf_token(self, path="/"):
        response = self.client.get(path)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode("utf-8")

    @staticmethod
    def synthetic_payload(name="API Dataset"):
        return {
            "dataset_name": name,
            "dataset_version": "v1",
            "seed": 73,
            "student_count": 4,
            "class_count": 2,
            "subject_count": 2,
            "room_count": 1,
            "rows_per_room": 1,
            "benches_per_row": 2,
            "seats_per_bench": 2,
            "exam_date": "2026-12-02",
        }

    def test_health_contract_and_records_are_public(self):
        health = self.client.get("/api/v1/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.get_json()["database"], "reachable")
        self.assertEqual(health.get_json()["background_queue"], "disabled")

        contract = self.client.get("/api/v1/openapi.json")
        self.assertEqual(contract.status_code, 200)
        self.assertIn("/jobs/{job_id}", contract.get_json()["paths"])
        self.assertEqual(contract.get_json()["openapi"], "3.1.0")

        records = self.client.get("/api/v1/datasets")
        self.assertEqual(records.status_code, 200)
        self.assertEqual(records.get_json()["data"], [])

    def test_background_job_progress_is_exposed_from_database(self):
        job, _ = create_job(
            "report_generation", {"run_id": 1, "format": "pdf", "progress_total": 100}
        )
        response = self.client.get(f"/api/v1/jobs/{job.id}")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["data"]
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["progress"]["percentage"], 0.0)

    def test_can_generate_and_page_persisted_synthetic_data(self):
        token = self.csrf_token("/")
        response = self.client.post(
            "/api/v1/datasets/synthetic",
            json=self.synthetic_payload(),
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        body = response.get_json()["data"]
        self.assertEqual(body["dataset"]["source_type"], "synthetic")
        self.assertEqual(body["exam"]["dataset_id"], body["dataset"]["id"])
        self.assertTrue(response.headers["Location"].endswith(f"/datasets/{body['dataset']['id']}"))
        self.assertEqual(Dataset.query.count(), 1)
        self.assertEqual(AuditLog.query.filter_by(action="synthetic_dataset_generated_api").count(), 1)
        self.assertEqual(ExamSession.query.count(), 1)
        session_payload = self.client.get("/api/v1/exam-sessions").get_json()
        self.assertEqual(session_payload["pagination"]["total"], 1)
        session_id = session_payload["data"][0]["id"]
        session_detail = self.client.get(f"/api/v1/exam-sessions/{session_id}")
        self.assertEqual(len(session_detail.get_json()["data"]["papers"]), 2)

        listing = self.client.get(
            "/api/v1/datasets?source_type=synthetic&page=1&per_page=1"
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.get_json()["pagination"]["total"], 1)
        self.assertEqual(len(listing.get_json()["data"]), 1)

        detail = self.client.get(response.headers["Location"])
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.get_json()["data"]["counts"]["students"], 4)

        invalid_page = self.client.get("/api/v1/datasets?per_page=101")
        self.assertEqual(invalid_page.status_code, 400)
        self.assertEqual(invalid_page.get_json()["error"]["code"], "invalid_pagination")

    def test_state_changes_require_csrf(self):
        missing_csrf = self.client.post(
            "/api/v1/datasets/synthetic", json=self.synthetic_payload()
        )
        self.assertEqual(missing_csrf.status_code, 400)
        self.assertEqual(missing_csrf.get_json()["error"]["code"], "csrf_failed")

    def test_untrusted_write_origin_is_rejected_as_json(self):
        response = self.client.post(
            "/api/v1/datasets/synthetic",
            json=self.synthetic_payload(),
            headers={"Origin": "https://untrusted.example"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"]["code"], "forbidden")
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_completed_run_can_be_published_and_allocations_are_paginated(self):
        dataset, exam = generate_synthetic_dataset(
            dataset_name="Publish API Dataset",
            dataset_version="v1",
            seed=91,
            student_count=2,
            class_count=2,
            subject_count=1,
            room_count=1,
            rows_per_room=1,
            benches_per_row=1,
            seats_per_bench=2,
            exam_date=date(2026, 12, 4),
        )
        registration = ExamRegistration.query.filter_by(exam_id=exam.id).first()
        seat = Seat.query.order_by(Seat.id).first()
        run = SeatingRun(
            exam_id=exam.id,
            exam_session_id=exam.exam_session_id,
            solver_version="api-test-v1",
            solver_status="FEASIBLE",
            solver_config={"adjacency_rule": "orthogonal"},
            validation_result={"overall_valid": True, "propositions": []},
            status="completed",
            generated_at=datetime.now(timezone.utc),
            created_by="api-planner",
        )
        db.session.add(run)
        db.session.flush()
        db.session.add(SeatAllocation(
            seating_run_id=run.id,
            student_id=registration.student_id,
            room_id=seat.room_id,
            seat_id=seat.id,
            hard_conflict=False,
            subject_relaxed=False,
        ))
        db.session.commit()

        token = self.csrf_token("/")
        published = self.client.post(
            f"/api/v1/seating-runs/{run.id}/publish",
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(published.status_code, 200, published.get_data(as_text=True))
        self.assertEqual(published.get_json()["data"]["status"], "published")
        self.assertEqual(published.get_json()["data"]["solver_status"], "FEASIBLE")
        self.assertEqual(
            AuditLog.query.filter_by(action="seating_run_published_api").count(), 1
        )

        allocations = self.client.get(
            f"/api/v1/seating-runs/{run.id}/allocations?page=1&per_page=1"
        )
        self.assertEqual(allocations.status_code, 200, allocations.get_data(as_text=True))
        body = allocations.get_json()
        self.assertEqual(body["pagination"]["total"], 1)
        self.assertEqual(body["data"][0]["student"]["id"], registration.student_id)
        self.assertEqual(body["data"][0]["hard_conflict"], False)

    def test_bulk_registration_rejects_overlapping_verified_session(self):
        dataset, exam = generate_synthetic_dataset(
            dataset_name="Conflict API Dataset",
            dataset_version="v1",
            seed=117,
            student_count=2,
            class_count=2,
            subject_count=1,
            room_count=1,
            rows_per_room=1,
            benches_per_row=1,
            seats_per_bench=2,
            exam_date=date(2026, 12, 6),
        )
        subject = Subject(
            code="SYN-CONFLICT-02",
            name="Synthetic Conflict Paper",
            source_type="synthetic",
            dataset=dataset,
        )
        db.session.add(subject)
        db.session.commit()
        token = self.csrf_token("/")

        session_response = self.client.post(
            "/api/v1/exam-sessions",
            json={
                "dataset_id": dataset.id,
                "exam_date": "2026-12-06",
                "start_time": "11:00",
                "end_time": "13:00",
                "session_name": "Overlapping Session",
                "academic_year": "2026-27",
            },
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(session_response.status_code, 201, session_response.get_data(as_text=True))
        session_id = session_response.get_json()["data"]["id"]
        paper_response = self.client.post(
            f"/api/v1/exam-sessions/{session_id}/papers",
            json={"subject_id": subject.id},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(paper_response.status_code, 201, paper_response.get_data(as_text=True))
        paper_id = paper_response.get_json()["data"]["id"]
        student = Student.query.order_by(Student.id).first()

        conflict = self.client.post(
            "/api/v1/exam-registrations/bulk",
            json={
                "verification_source": "controller_of_examinations",
                "registrations": [{"student_id": student.id, "exam_id": paper_id}],
            },
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(conflict.status_code, 422)
        self.assertEqual(conflict.get_json()["error"]["code"], "registration_conflict")
        self.assertEqual(
            ExamRegistration.query.filter_by(student_id=student.id).count(),
            1,
        )


if __name__ == "__main__":
    unittest.main()
