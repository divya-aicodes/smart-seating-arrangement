"""Database schema, integrity, and transactional seating-run tests."""

import unittest
from datetime import date, time

from flask import Flask
from sqlalchemy.exc import IntegrityError

from database_service import (
    PersistenceError,
    complete_seating_run,
    create_seating_run,
    publish_seating_run,
)
from extensions import db
from models import (
    Dataset,
    Exam,
    ExamRegistration,
    Room,
    Seat,
    SeatAllocation,
    SeatingRun,
    Student,
    Subject,
)


class TestDatabasePersistence(unittest.TestCase):

    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

        self.dataset = Dataset(
            name="Institutional import",
            version="2026-10-12",
            source_type="real",
            status="active",
        )
        self.subject = Subject(code="DM-101", name="Discrete Mathematics")
        self.student = Student(
            student_code="STU-001",
            full_name="Test Student",
            class_name="CSE-A",
            department="CSE",
            source_type="real",
            dataset=self.dataset,
        )
        self.exam = Exam(
            subject=self.subject,
            exam_date=date(2026, 12, 1),
            start_time=time(9, 0),
            end_time=time(12, 0),
            session_name="Morning",
            academic_year="2026-27",
            status="scheduled",
        )
        self.room = Room(
            room_number="A-101",
            building="Academic Block",
            floor="1",
            rows=1,
            benches_per_row=1,
            seats_per_bench=2,
            capacity=2,
        )
        self.seat = Seat(
            room=self.room,
            seat_code="A-101-R1-B1-S1",
            row_number=1,
            bench_number=1,
            seat_number=1,
            accessible=True,
        )
        self.registration = ExamRegistration(
            exam=self.exam,
            student=self.student,
            status="eligible",
        )
        db.session.add_all([
            self.dataset,
            self.subject,
            self.student,
            self.exam,
            self.room,
            self.seat,
            self.registration,
        ])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_expected_persistent_tables_exist(self):
        expected = {
            "audit_logs",
            "data_imports",
            "datasets",
            "exam_registrations",
            "exams",
            "invigilator_assignments",
            "invigilators",
            "rooms",
            "seat_allocations",
            "seating_runs",
            "seats",
            "students",
            "subjects",
            "users",
        }
        self.assertEqual(set(db.metadata.tables), expected)

    def test_real_dataset_lineage_is_persisted(self):
        saved = db.session.get(Student, self.student.id)
        self.assertEqual(saved.dataset.source_type, "real")
        self.assertEqual(saved.dataset.version, "2026-10-12")

    def test_duplicate_student_code_is_rejected(self):
        duplicate = Student(
            student_code="STU-001",
            class_name="CSE-B",
            source_type="real",
        )
        db.session.add(duplicate)
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_room_capacity_cannot_exceed_layout(self):
        invalid_room = Room(
            room_number="BAD-1",
            building="Academic Block",
            rows=1,
            benches_per_row=1,
            seats_per_bench=1,
            capacity=2,
        )
        db.session.add(invalid_room)
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_completed_run_and_allocations_are_transactional(self):
        run = create_seating_run(
            self.exam.id,
            {"adjacency_rule": "orthogonal"},
            created_by="phase-2-test",
        )
        completed = complete_seating_run(
            run.id,
            [{
                "student_id": self.student.id,
                "seat_id": self.seat.id,
                "subject_relaxed": False,
            }],
            {"overall_valid": True, "propositions": []},
        )
        self.assertEqual(completed.status, "completed")
        self.assertEqual(SeatAllocation.query.filter_by(seating_run_id=run.id).count(), 1)

        published = publish_seating_run(run.id)
        self.assertEqual(published.status, "published")
        self.assertIsNotNone(published.published_at)

    def test_failed_hard_constraint_rolls_back_allocations(self):
        run = create_seating_run(self.exam.id, {"adjacency_rule": "orthogonal"})
        with self.assertRaises(PersistenceError):
            complete_seating_run(
                run.id,
                [{
                    "student_id": self.student.id,
                    "seat_id": self.seat.id,
                    "hard_conflict": True,
                }],
                {"overall_valid": True},
            )

        persisted = db.session.get(SeatingRun, run.id)
        self.assertEqual(persisted.status, "pending")
        self.assertEqual(SeatAllocation.query.filter_by(seating_run_id=run.id).count(), 0)


if __name__ == "__main__":
    unittest.main()
