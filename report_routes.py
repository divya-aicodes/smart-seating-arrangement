"""Operational dashboard and persisted report downloads."""

import hashlib

from flask import Blueprint, abort, current_app, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from extensions import db
from background_service import create_job, fail_job
from background_tasks import enqueue_job
from models import BackgroundJob, Dataset, Exam, ExamSession, SeatAllocation, SeatingRun
from pagination import page_navigation, paginate
from reporting import ReportNotAvailable, build_excel_report, build_pdf_report, load_report_data, load_report_page
from security import record_audit


report_bp = Blueprint("reports", __name__, url_prefix="/reports")


def _report_or_404(run_id: int):
    try:
        return load_report_data(run_id)
    except ReportNotAvailable as exc:
        abort(404, description=str(exc))


def _large_report_required(run_id):
    if not current_app.config["BACKGROUND_JOBS_ENABLED"]:
        return False
    allocation_count = db.session.scalar(
        select(func.count(SeatAllocation.id)).where(SeatAllocation.seating_run_id == run_id)
    ) or 0
    return allocation_count >= current_app.config["BACKGROUND_REPORT_MIN_ALLOCATIONS"]


def _queue_large_report(run_id, report_format):
    run = db.session.get(SeatingRun, run_id)
    if run is None or run.status not in {"completed", "published"}:
        abort(404)
    key_source = f"report:{run_id}:{report_format}:{run.updated_at.isoformat()}"
    job, reused = create_job(
        "report_generation",
        {"run_id": run_id, "format": report_format, "progress_total": 100},
        idempotency_key=hashlib.sha256(key_source.encode("utf-8")).hexdigest(),
        created_by=current_user.username,
    )
    if not reused:
        try:
            enqueue_job(job)
        except Exception as exc:
            fail_job(job.id, "The report could not be queued. Check Redis and worker availability.")
            abort(503, description="The background report could not be queued.")
    return redirect(url_for("jobs.detail", job_id=job.id))


@report_bp.post("/runs/<int:run_id>/queue/<report_format>")
def queue_report(run_id: int, report_format: str):
    if report_format not in {"pdf", "xlsx"}:
        abort(404)
    if not _large_report_required(run_id):
        return redirect(
            url_for("reports.run_pdf" if report_format == "pdf" else "reports.run_excel", run_id=run_id)
        )
    return _queue_large_report(run_id, report_format)


@report_bp.get("")
def dashboard():
    query = request.args.get("q", "").strip()[:100]
    solver_status = request.args.get("solver_status", "").strip().upper()
    allowed_solver_statuses = {
        "OPTIMAL", "FEASIBLE", "FEASIBLE_WITH_RELAXATION", "INFEASIBLE",
        "TIME_LIMIT_REACHED", "UNKNOWN",
    }
    statement = (
        select(SeatingRun)
        .join(SeatingRun.session)
        .outerjoin(ExamSession.dataset)
        .where(SeatingRun.status.in_(["completed", "published"]))
        .options(
            joinedload(SeatingRun.exam).joinedload(Exam.dataset),
            joinedload(SeatingRun.session).joinedload(ExamSession.dataset),
        )
        .order_by(SeatingRun.generated_at.desc(), SeatingRun.id.desc())
    )
    if query:
        pattern = f"%{query}%"
        statement = statement.where(
            or_(
                ExamSession.session_name.ilike(pattern),
                Dataset.name.ilike(pattern),
                SeatingRun.created_by.ilike(pattern),
            )
        )
    if solver_status in allowed_solver_statuses:
        statement = statement.where(SeatingRun.solver_status == solver_status)
    else:
        solver_status = ""
    runs = paginate(statement, per_page=20)
    run_ids = [run.id for run in runs.items]
    allocation_counts = dict(
        db.session.execute(
            select(SeatAllocation.seating_run_id, func.count(SeatAllocation.id))
            .where(SeatAllocation.seating_run_id.in_(run_ids or [-1]))
            .group_by(SeatAllocation.seating_run_id)
        ).all()
    )
    reportable = SeatingRun.status.in_(["completed", "published"])
    stats = {
        "runs": db.session.scalar(
            select(func.count()).select_from(SeatingRun).where(reportable)
        ) or 0,
        "allocations": db.session.scalar(
            select(func.count(SeatAllocation.id))
            .join(SeatingRun, SeatAllocation.seating_run_id == SeatingRun.id)
            .where(reportable)
        ) or 0,
        "published": db.session.scalar(
            select(func.count()).select_from(SeatingRun).where(SeatingRun.status == "published")
        ) or 0,
        "exams": db.session.scalar(
            select(func.count(func.distinct(SeatingRun.exam_session_id))).where(reportable)
        ) or 0,
    }
    return render_template(
        "reports_dashboard.html",
        runs=runs,
        navigation=page_navigation(runs),
        allocation_counts=allocation_counts,
        stats=stats,
        query=query,
        selected_solver_status=solver_status,
        solver_statuses=sorted(allowed_solver_statuses),
        export_jobs=db.session.scalars(
            select(BackgroundJob)
            .where(BackgroundJob.job_type == "report_generation")
            .order_by(BackgroundJob.created_at.desc())
            .limit(6)
        ).all(),
    )


@report_bp.get("/runs/<int:run_id>")
def run_detail(run_id: int):
    try:
        report = load_report_page(run_id, query=request.args.get("q", "").strip())
    except ReportNotAvailable as exc:
        abort(404, description=str(exc))
    return render_template("report_detail.html", report=report)


@report_bp.get("/runs/<int:run_id>.pdf")
def run_pdf(run_id: int):
    if _large_report_required(run_id):
        return render_template("report_queue.html", run_id=run_id, report_format="pdf")
    report = _report_or_404(run_id)
    output = build_pdf_report(report)
    record_audit("seating_report_exported", "seating_run", run_id, after_values={"format": "pdf"})
    return send_file(
        output,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"seating-run-{run_id}.pdf",
        max_age=0,
    )


@report_bp.get("/runs/<int:run_id>.xlsx")
def run_excel(run_id: int):
    if _large_report_required(run_id):
        return render_template("report_queue.html", run_id=run_id, report_format="xlsx")
    report = _report_or_404(run_id)
    output = build_excel_report(report)
    record_audit("seating_report_exported", "seating_run", run_id, after_values={"format": "xlsx"})
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"seating-run-{run_id}.xlsx",
        max_age=0,
    )
