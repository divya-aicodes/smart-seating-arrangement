"""Academic examination sessions, papers, and verified registration rules."""

from __future__ import annotations

from datetime import date, datetime, time, timezone

from sqlalchemy import select

from extensions import db
from models import Dataset, Exam, ExamRegistration, ExamSession, Student, Subject


class AcademicValidationError(ValueError):
    """Raised when examination data would violate an academic rule."""


def create_exam_session(
    *,
    dataset: Dataset,
    exam_date: date,
    start_time: time,
    end_time: time,
    session_name: str,
    academic_year: str,
    status: str = "scheduled",
) -> ExamSession:
    session_name = (session_name or "").strip()
    academic_year = (academic_year or "").strip()
    if not session_name or len(session_name) > 80:
        raise AcademicValidationError("Session name is required and must be 80 characters or fewer.")
    if not academic_year or len(academic_year) > 20:
        raise AcademicValidationError("Academic year is required and must be 20 characters or fewer.")
    if end_time <= start_time:
        raise AcademicValidationError("Exam session end time must be later than its start time.")
    if status not in {"draft", "scheduled", "completed", "cancelled"}:
        raise AcademicValidationError("Exam session status is invalid.")

    existing = db.session.scalar(
        select(ExamSession).where(
            ExamSession.dataset_id == dataset.id,
            ExamSession.exam_date == exam_date,
            ExamSession.start_time == start_time,
            ExamSession.end_time == end_time,
            ExamSession.session_name == session_name,
        )
    )
    if existing is not None:
        raise AcademicValidationError("This dataset already has the same examination session.")

    session = ExamSession(
        institution_id=dataset.institution_id,
        dataset=dataset,
        exam_date=exam_date,
        start_time=start_time,
        end_time=end_time,
        session_name=session_name,
        academic_year=academic_year,
        status=status,
        source_type=dataset.source_type,
    )
    db.session.add(session)
    db.session.flush()
    return session


def create_exam_paper(*, session: ExamSession, subject: Subject) -> Exam:
    if not subject.active:
        raise AcademicValidationError(f"Subject '{subject.code}' is inactive.")
    if subject.institution_id and session.institution_id != subject.institution_id:
        raise AcademicValidationError("The subject and examination session belong to different institutions.")
    if subject.source_type != session.source_type:
        raise AcademicValidationError("The subject and examination session have different data sources.")
    existing = db.session.scalar(
        select(Exam).where(
            Exam.exam_session_id == session.id,
            Exam.subject_id == subject.id,
        )
    )
    if existing is not None:
        return existing

    paper = Exam(
        institution_id=session.institution_id,
        dataset_id=session.dataset_id,
        session=session,
        subject=subject,
        # Compatibility snapshots for clients that still read scheduling from /exams.
        exam_date=session.exam_date,
        start_time=session.start_time,
        end_time=session.end_time,
        session_name=session.session_name,
        academic_year=session.academic_year,
        status=session.status,
        source_type=session.source_type,
    )
    db.session.add(paper)
    db.session.flush()
    return paper


def _overlapping_verified_registrations(
    *, student_ids: list[int], session: ExamSession
) -> list[ExamRegistration]:
    if not student_ids:
        return []
    return db.session.scalars(
        select(ExamRegistration)
        .join(ExamSession, ExamRegistration.exam_session_id == ExamSession.id)
        .where(
            ExamRegistration.student_id.in_(student_ids),
            ExamRegistration.exam_session_id != session.id,
            ExamRegistration.verification_status == "verified",
            ExamRegistration.status.in_(["registered", "eligible"]),
            ExamSession.status != "cancelled",
            ExamSession.exam_date == session.exam_date,
            ExamSession.start_time < session.end_time,
            ExamSession.end_time > session.start_time,
        )
    ).all()


def register_student_for_paper(
    *,
    student: Student,
    paper: Exam,
    verification_source: str,
    status: str = "eligible",
) -> ExamRegistration:
    """Create or verify one exact student-paper registration after conflict checks."""
    return register_students_for_session(
        [(student, paper)],
        verification_source=verification_source,
        status=status,
    )[0]


def register_students_for_session(
    entries: list[tuple[Student, Exam]],
    *,
    verification_source: str,
    status: str = "eligible",
) -> list[ExamRegistration]:
    """Verify a bounded batch for one session with set-based conflict queries."""
    if not entries:
        return []
    source = (verification_source or "").strip()
    if not source or len(source) > 80:
        raise AcademicValidationError(
            "A verification source is required and must be 80 characters or fewer."
        )
    if status not in {"registered", "eligible"}:
        raise AcademicValidationError("Verified registrations must be registered or eligible.")
    sessions = {paper.exam_session_id for _, paper in entries}
    if None in sessions or len(sessions) != 1:
        raise AcademicValidationError("A registration batch must target one examination session.")
    session = entries[0][1].session
    if session is None:
        raise AcademicValidationError("The examination paper is not linked to a session.")

    student_ids: list[int] = []
    seen_student_ids: set[int] = set()
    for student, paper in entries:
        if student.id is None:
            raise AcademicValidationError("Students must be persisted before registration.")
        if student.id in seen_student_ids:
            raise AcademicValidationError(
                f"Student '{student.student_code}' appears more than once in the registration batch."
            )
        seen_student_ids.add(student.id)
        student_ids.append(student.id)
        if not student.active:
            raise AcademicValidationError(f"Student '{student.student_code}' is inactive.")
        if paper.session is None or paper.subject is None:
            raise AcademicValidationError(
                "The examination paper is not linked to a session and subject."
            )
        if student.institution_id and paper.session.institution_id != student.institution_id:
            raise AcademicValidationError(
                "The student and examination paper belong to different institutions."
            )
        if student.source_type != paper.source_type:
            raise AcademicValidationError(
                "The student and examination paper have different data sources."
            )

    # PostgreSQL row locks serialize competing registration batches for the same
    # students so two overlapping sessions cannot both pass the conflict query.
    db.session.execute(
        select(Student.id)
        .where(Student.id.in_(student_ids))
        .order_by(Student.id)
        .with_for_update()
    ).all()

    existing_by_student = {
        registration.student_id: registration
        for registration in db.session.scalars(
            select(ExamRegistration).where(
                ExamRegistration.exam_session_id == session.id,
                ExamRegistration.student_id.in_(student_ids),
            )
        ).all()
    }
    conflicts = _overlapping_verified_registrations(
        student_ids=student_ids, session=session
    )
    if conflicts:
        conflict = conflicts[0]
        student = next(item for item, _ in entries if item.id == conflict.student_id)
        other = conflict.session
        raise AcademicValidationError(
            f"Student '{student.student_code}' already has a verified registration in "
            f"'{other.session_name}' from {other.start_time.strftime('%H:%M')} to "
            f"{other.end_time.strftime('%H:%M')} on {other.exam_date.isoformat()}."
        )

    verified_at = datetime.now(timezone.utc)
    registrations: list[ExamRegistration] = []
    for student, paper in entries:
        registration = existing_by_student.get(student.id)
        if registration is not None and registration.exam_id != paper.id:
            raise AcademicValidationError(
                f"Student '{student.student_code}' is already registered for another paper "
                f"in session '{session.session_name}'."
            )
        registration = registration or ExamRegistration(student=student, exam=paper)
        registration.exam = paper
        registration.session = session
        registration.subject = paper.subject
        registration.status = status
        registration.verification_status = "verified"
        registration.verification_source = source
        registration.verified_at = verified_at
        db.session.add(registration)
        registrations.append(registration)
    db.session.flush()
    return registrations
