"""Read-only institutional administration registers backed by persisted data."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.exc import IntegrityError

from auth_routes import password_validation_error
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
    User,
)
from pagination import page_navigation, paginate


admin_bp = Blueprint("administration", __name__, url_prefix="/administration")


def _counts(*models):
    counts = {}
    for model in models:
        statement = select(func.count()).select_from(model)
        if model is Institution:
            statement = statement.where(Institution.id == current_user.institution_id)
        elif model is Specialisation:
            statement = statement.join(Department).where(
                Department.institution_id == current_user.institution_id
            )
        elif model is Section:
            statement = statement.join(Department).where(
                Department.institution_id == current_user.institution_id
            )
        elif model is ExamRegistration:
            statement = statement.join(ExamSession).where(
                ExamSession.institution_id == current_user.institution_id
            )
        elif model is Seat:
            statement = statement.join(Room).where(
                Room.institution_id == current_user.institution_id
            )
        counts[model.__tablename__] = db.session.scalar(statement) or 0
    return counts


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
        .join(Section.department)
        .where(Department.institution_id == current_user.institution_id)
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


@admin_bp.route("/users", methods=["GET", "POST"])
def users():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip() or None
        role = request.form.get("role", "viewer").strip()
        password = request.form.get("password", "")
        error = password_validation_error(password)
        if not username or len(username) > 80:
            error = "Username is required and must be 80 characters or fewer."
        elif role not in {"admin", "planner", "viewer"}:
            error = "Select a valid account role."
        if error:
            flash(error, "error")
            return redirect(url_for("administration.users"))
        account = User(
            institution_id=current_user.institution_id,
            username=username,
            email=email,
            role=role,
            active=True,
        )
        account.set_password(password)
        try:
            db.session.add(account)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("That username or email is already registered.", "error")
        else:
            from security import record_audit

            record_audit(
                "user_created",
                "user",
                account.id,
                after_values={"username": account.username, "role": account.role},
            )
            flash(f"Created {role} account '{username}'.", "success")
        return redirect(url_for("administration.users"))
    account_page = paginate(
        select(User)
        .where(User.institution_id == current_user.institution_id)
        .order_by(User.active.desc(), User.username),
        per_page=30,
    )
    return render_template(
        "administration_users.html",
        users=account_page,
        navigation=page_navigation(account_page),
    )
