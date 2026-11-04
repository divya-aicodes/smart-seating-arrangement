"""Transactional persistence operations for seating runs."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from extensions import db
from models import Exam, ExamRegistration, ExamSession, Seat, SeatAllocation, SeatingRun, Student


class PersistenceError(RuntimeError):
    """Raised when a requested database state transition is invalid."""


def create_seating_run(
    exam_session_id: int,
    solver_config: dict[str, Any],
    *,
    solver_version: str = "greedy-v1",
    created_by: str | None = None,
) -> SeatingRun:
    session = db.session.get(ExamSession, exam_session_id)
    if session is None or session.status not in {"draft", "scheduled"}:
        raise PersistenceError(f"Exam session {exam_session_id} is unavailable.")
    primary_paper = db.session.scalar(
        select(Exam)
        .where(Exam.exam_session_id == session.id)
        .order_by(Exam.id)
        .limit(1)
    )
    if primary_paper is None:
        raise PersistenceError(f"Exam session {exam_session_id} has no papers.")
    run = SeatingRun(
        institution_id=session.institution_id,
        exam_id=primary_paper.id,
        exam_session_id=session.id,
        solver_version=solver_version,
        solver_config=solver_config,
        status="pending",
        created_by=created_by,
    )
    try:
        db.session.add(run)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return run


def complete_seating_run(
    run_id: int,
    allocations: list[dict[str, Any]],
    validation_result: dict[str, Any],
    *,
    solver_status: str = "FEASIBLE",
) -> SeatingRun:
    """Atomically save a validated run and its student-to-seat allocations."""
    run = db.session.get(SeatingRun, run_id)
    if run is None:
        raise PersistenceError(f"Seating run {run_id} does not exist.")
    if run.status not in {"pending", "running"}:
        raise PersistenceError(f"Seating run {run_id} cannot be completed from '{run.status}'.")
    if not validation_result.get("overall_valid", False):
        raise PersistenceError("A seating run with failed hard constraints cannot be completed.")
    if not allocations:
        raise PersistenceError("A completed seating run must contain at least one allocation.")
    if solver_status not in {"OPTIMAL", "FEASIBLE", "FEASIBLE_WITH_RELAXATION"}:
        raise PersistenceError(f"'{solver_status}' is not a publishable solver outcome.")

    try:
        seen_students: set[int] = set()
        seen_seats: set[int] = set()
        for item in allocations:
            student_id = int(item["student_id"])
            seat_id = int(item["seat_id"])
            if student_id in seen_students:
                raise PersistenceError(f"Student {student_id} appears more than once.")
            if seat_id in seen_seats:
                raise PersistenceError(f"Seat {seat_id} appears more than once.")
            if item.get("hard_conflict", False):
                raise PersistenceError("Hard-conflict allocations cannot be persisted as completed.")

            student = db.session.get(Student, student_id)
            if student is None or not student.active:
                raise PersistenceError(f"Student {student_id} is unavailable.")
            registration = db.session.scalar(
                select(ExamRegistration).where(
                    ExamRegistration.exam_session_id == run.exam_session_id,
                    ExamRegistration.student_id == student_id,
                    ExamRegistration.status.in_(["registered", "eligible"]),
                    ExamRegistration.verification_status == "verified",
                )
            )
            if registration is None:
                raise PersistenceError(
                    f"Student {student_id} has no verified registration for exam "
                    f"session {run.exam_session_id}."
                )
            seat = db.session.get(Seat, seat_id)
            if seat is None or not seat.active:
                raise PersistenceError(f"Seat {seat_id} is unavailable.")

            seen_students.add(student_id)
            seen_seats.add(seat_id)
            db.session.add(
                SeatAllocation(
                    seating_run_id=run.id,
                    student_id=student_id,
                    room_id=seat.room_id,
                    seat_id=seat.id,
                    hard_conflict=False,
                    subject_relaxed=bool(item.get("subject_relaxed", False)),
                )
            )

        run.validation_result = validation_result
        run.solver_status = solver_status
        run.status = "completed"
        run.generated_at = datetime.now(timezone.utc)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return run


def publish_seating_run(run_id: int) -> SeatingRun:
    run = db.session.get(SeatingRun, run_id)
    if run is None:
        raise PersistenceError(f"Seating run {run_id} does not exist.")
    if run.status != "completed":
        raise PersistenceError("Only a completed seating run can be published.")
    if not run.allocations:
        raise PersistenceError("A seating run without allocations cannot be published.")
    if not (run.validation_result or {}).get("overall_valid", False):
        raise PersistenceError("A seating run with failed validation cannot be published.")

    try:
        run.status = "published"
        run.published_at = datetime.now(timezone.utc)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return run
