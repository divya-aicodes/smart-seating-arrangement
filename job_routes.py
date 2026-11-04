"""Progress, cancellation, retry, and artifact access for background work."""

from io import BytesIO

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, send_file, url_for
from sqlalchemy import func, select

from background_service import fail_job, request_cancellation, reset_for_retry
from background_tasks import enqueue_job
from extensions import db
from job_storage import JobStorageError, get_bytes, presigned_download
from models import BackgroundJob
from security import record_audit


job_bp = Blueprint("jobs", __name__, url_prefix="/jobs")
job_api_bp = Blueprint("job_api", __name__, url_prefix="/api/v1/jobs")


def _job_or_404(job_id):
    job = db.session.get(BackgroundJob, job_id)
    if job is None:
        abort(404, description="The requested background job does not exist.")
    return job


def job_json(job):
    percentage = round(job.progress_current * 100 / max(1, job.progress_total), 1)
    public_result = None
    if job.status == "completed" and job.result:
        public_result = {
            key: value for key, value in job.result.items() if key != "artifact_key"
        }
    return {
        "id": job.id,
        "type": job.job_type,
        "status": job.status,
        "progress": {
            "current": job.progress_current,
            "total": job.progress_total,
            "percentage": percentage,
            "message": job.progress_message,
        },
        "attempts": job.attempts,
        "cancellation_requested": job.cancellation_requested,
        "result": public_result,
        "error": job.error_message if job.status == "failed" else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


@job_bp.get("/<int:job_id>")
def detail(job_id):
    return render_template("job_detail.html", job=_job_or_404(job_id))


@job_bp.get("/<int:job_id>/status")
def status(job_id):
    return jsonify({"data": job_json(_job_or_404(job_id))})


@job_api_bp.get("/<int:job_id>")
def api_status(job_id):
    return jsonify({"data": job_json(_job_or_404(job_id))})


@job_api_bp.get("")
def api_list():
    try:
        page = max(1, int(request.args.get("page", "1")))
        per_page = min(100, max(1, int(request.args.get("per_page", "25"))))
    except ValueError:
        abort(400, description="page and per_page must be whole numbers.")
    statement = select(BackgroundJob)
    status_filter = request.args.get("status", "").strip().lower()
    type_filter = request.args.get("type", "").strip().lower()
    if status_filter:
        if status_filter not in {"queued", "running", "completed", "failed", "cancelled"}:
            abort(400, description="Unsupported background job status.")
        statement = statement.where(BackgroundJob.status == status_filter)
    if type_filter:
        if type_filter not in {
            "import_stage", "import_commit", "seating_optimization", "report_generation"
        }:
            abort(400, description="Unsupported background job type.")
        statement = statement.where(BackgroundJob.job_type == type_filter)
    total = db.session.scalar(
        select(func.count()).select_from(statement.subquery())
    ) or 0
    jobs = db.session.scalars(
        statement.order_by(BackgroundJob.created_at.desc())
        .limit(per_page)
        .offset((page - 1) * per_page)
    ).all()
    return jsonify({
        "data": [job_json(job) for job in jobs],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page,
        },
    })


@job_bp.post("/<int:job_id>/cancel")
def cancel(job_id):
    job = request_cancellation(job_id)
    if job is None:
        abort(404)
    record_audit("background_job_cancel_requested", "background_job", job.id)
    return redirect(url_for("jobs.detail", job_id=job.id))


@job_bp.post("/<int:job_id>/retry")
def retry(job_id):
    if not current_app.config["BACKGROUND_JOBS_ENABLED"]:
        abort(503, description="Background workers are not configured.")
    job = reset_for_retry(job_id)
    if job is None:
        abort(409, description="Only failed or cancelled jobs can be retried.")
    try:
        enqueue_job(job)
    except Exception:
        fail_job(job.id, "The retry could not be queued. Check Redis and worker availability.")
        abort(503, description="The background job retry could not be queued.")
    record_audit("background_job_retried", "background_job", job.id)
    return redirect(url_for("jobs.detail", job_id=job.id))


@job_bp.get("/<int:job_id>/download")
def download(job_id):
    job = _job_or_404(job_id)
    result = job.result or {}
    if job.status != "completed" or not result.get("artifact_key"):
        abort(409, description="The requested artifact is not ready.")
    signed_url = presigned_download(
        result["artifact_key"], result["filename"], result["content_type"]
    )
    if signed_url:
        return redirect(signed_url)
    try:
        data = get_bytes(result["artifact_key"])
    except JobStorageError as exc:
        abort(404, description=str(exc))
    return send_file(
        BytesIO(data),
        mimetype=result["content_type"],
        as_attachment=True,
        download_name=result["filename"],
        max_age=0,
    )
