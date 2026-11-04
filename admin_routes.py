"""Read-only institutional administration registers backed by persisted data."""

from flask import Blueprint, render_template
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectinload

from extensions import db
from models import (
    AcademicYear,
    AuditLog,
    BackgroundJob,
    DataImport,
    Department,
    Exam,
    ExamRegistration,
    ExamSession,
    Institution,
    Room,
    Seat,
    Section,
    SeatingRun,
    Specialisation,
    Student,
    Subject,
)
from pagination import page_navigation, paginate


admin_bp = Blueprint("administration", __name__, url_prefix="/administration")


def _counts(*models):
    return {
        model.__tablename__: db.session.scalar(select(func.count()).select_from(model)) or 0
        for model in models
    }


@admin_bp.get("")
def dashboard():
    stats = _counts(Institution, Student, ExamSession, Room, SeatingRun, BackgroundJob)
    jobs = db.session.scalars(
        select(BackgroundJob).order_by(BackgroundJob.created_at.desc()).limit(8)
    ).all()
    audits = db.session.scalars(
        select(AuditLog).order_by(AuditLog.created_at.desc()).limit(8)
    ).all()
    return render_template("administration_dashboard.html", stats=stats, jobs=jobs, audits=audits)


@admin_bp.get("/academics")
def academics():
    departments = paginate(
        select(Department)
        .options(selectinload(Department.specialisations))
        .order_by(Department.code),
        param="department_page",
        per_page=20,
    )
    subjects = paginate(
        select(Subject).order_by(Subject.code), param="subject_page", per_page=20
    )
    sections = paginate(
        select(Section)
        .options(
            joinedload(Section.department),
            joinedload(Section.specialisation),
            joinedload(Section.semester),
        )
        .order_by(Section.id.desc()),
        param="section_page",
        per_page=20,
    )
    return render_template(
        "administration_academics.html",
        departments=departments,
        departments_nav=page_navigation(departments),
        subjects=subjects,
        subjects_nav=page_navigation(subjects),
        sections=sections,
        sections_nav=page_navigation(sections),
        totals=_counts(Department, Specialisation, AcademicYear, Section, Subject),
    )


@admin_bp.get("/examinations")
def examinations():
    sessions = paginate(
        select(ExamSession)
        .options(joinedload(ExamSession.dataset))
        .order_by(ExamSession.exam_date.desc(), ExamSession.start_time.desc()),
        per_page=20,
    )
    session_ids = [item.id for item in sessions.items]
    paper_counts = dict(
        db.session.execute(
            select(Exam.exam_session_id, func.count(Exam.id))
            .where(Exam.exam_session_id.in_(session_ids or [-1]))
            .group_by(Exam.exam_session_id)
        ).all()
    )
    registration_counts = dict(
        db.session.execute(
            select(ExamRegistration.exam_session_id, func.count(ExamRegistration.id))
            .where(ExamRegistration.exam_session_id.in_(session_ids or [-1]))
            .group_by(ExamRegistration.exam_session_id)
        ).all()
    )
    return render_template(
        "administration_examinations.html",
        sessions=sessions,
        navigation=page_navigation(sessions),
        paper_counts=paper_counts,
        registration_counts=registration_counts,
        totals=_counts(ExamSession, Exam, ExamRegistration, SeatingRun),
    )


@admin_bp.get("/rooms")
def rooms():
    room_page = paginate(
        select(Room).options(joinedload(Room.dataset)).order_by(Room.building, Room.room_number),
        per_page=25,
    )
    room_ids = [item.id for item in room_page.items]
    seat_counts = dict(
        db.session.execute(
            select(Seat.room_id, func.count(Seat.id))
            .where(Seat.room_id.in_(room_ids or [-1]), Seat.active.is_(True))
            .group_by(Seat.room_id)
        ).all()
    )
    accessible_counts = dict(
        db.session.execute(
            select(Seat.room_id, func.count(Seat.id))
            .where(
                Seat.room_id.in_(room_ids or [-1]),
                Seat.active.is_(True),
                Seat.accessible.is_(True),
            )
            .group_by(Seat.room_id)
        ).all()
    )
    return render_template(
        "administration_rooms.html",
        rooms=room_page,
        navigation=page_navigation(room_page),
        seat_counts=seat_counts,
        accessible_counts=accessible_counts,
        totals=_counts(Room, Seat),
    )


@admin_bp.get("/jobs")
def jobs():
    job_page = paginate(
        select(BackgroundJob).order_by(BackgroundJob.created_at.desc()), per_page=25
    )
    return render_template(
        "administration_jobs.html",
        jobs=job_page,
        navigation=page_navigation(job_page),
        totals=_counts(BackgroundJob, DataImport),
    )


@admin_bp.get("/audit")
def audit():
    audit_page = paginate(
        select(AuditLog).order_by(AuditLog.created_at.desc()), per_page=30
    )
    return render_template(
        "administration_audit.html",
        audits=audit_page,
        navigation=page_navigation(audit_page),
    )
