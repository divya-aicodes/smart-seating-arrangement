"""Celery task implementations that reuse existing domain services."""

from __future__ import annotations

from datetime import date, time

from sqlalchemy import select

from celery_app import celery_app


def _run_job(job_id, operation):
    from app import app
    from background_service import claim_job, fail_job

    with app.app_context():
        job = claim_job(job_id)
        if job is None:
            return
        try:
            operation(app, job)
        except Exception as exc:
            app.logger.exception("Background job %s failed", job_id)
            from extensions import db

            db.session.rollback()
            fail_job(job_id, str(exc) or exc.__class__.__name__)
            raise


@celery_app.task(name="seatplan.stage_import")
def stage_import_job(job_id):
    def operation(_app, job):
        from werkzeug.datastructures import FileStorage

        from background_service import complete_job, update_progress
        from data_service import stage_student_import
        from job_storage import delete, open_seekable

        payload = job.payload

        def progress(current, total, message):
            if not update_progress(job.id, current, total, message):
                raise RuntimeError("Import cancelled safely.")

        with open_seekable(payload["storage_key"]) as stream:
            import_record, reused = stage_student_import(
                FileStorage(stream=stream, filename=payload["filename"]),
                dataset_name=payload["dataset_name"],
                dataset_version=payload["dataset_version"],
                exam_date=date.fromisoformat(payload["exam_date"]),
                start_time=time.fromisoformat(payload["start_time"]),
                end_time=time.fromisoformat(payload["end_time"]),
                session_name=payload["session_name"],
                academic_year=payload["academic_year"],
                chunk_size=payload["chunk_size"],
                progress_callback=progress,
                retry_existing=job.attempts > 1,
            )
        complete_job(job.id, {
            "import_id": import_record.id,
            "reused": reused,
            "result_url": f"/data#data-import-{import_record.id}",
        })
        delete(payload["storage_key"])

    _run_job(job_id, operation)


@celery_app.task(name="seatplan.commit_import")
def commit_import_job(job_id):
    def operation(_app, job):
        from background_service import complete_job, update_progress
        from data_service import commit_staged_student_import

        def progress(current, total, message):
            if not update_progress(job.id, current, total, message):
                raise RuntimeError("Import commit cancelled safely.")

        dataset, exam, import_record = commit_staged_student_import(
            int(job.payload["import_id"]), progress_callback=progress
        )
        complete_job(job.id, {
            "import_id": import_record.id,
            "dataset_id": dataset.id,
            "exam_session_id": exam.exam_session_id,
            "result_url": "/data",
        })

    _run_job(job_id, operation)


@celery_app.task(name="seatplan.optimize")
def optimize_job(job_id):
    def operation(app, job):
        from background_service import complete_job, update_progress
        from extensions import db
        from models import SeatingRun

        existing_run = db.session.scalar(
            select(SeatingRun)
            .where(SeatingRun.created_by == f"background-job:{job.id}")
            .order_by(SeatingRun.id.desc())
        )
        if existing_run is not None:
            complete_job(job.id, {
                "seating_run_id": existing_run.id,
                "solver_status": existing_run.solver_status,
                "result_url": f"/reports/runs/{existing_run.id}",
            })
            return
        if not update_progress(job.id, 10, 100, "Loading verified registrations and rooms"):
            return
        form = dict(job.payload["form"])
        form["room_ids"] = job.payload["form"].get("room_ids", [])
        form["_background_job_id"] = str(job.id)
        with app.test_request_context("/assign", method="POST", data=form):
            from app import assign

            if not update_progress(job.id, 25, 100, "Running feasibility and optimization"):
                return
            response = app.make_response(assign())
        if response.status_code >= 400:
            raise RuntimeError(f"Optimization stopped with HTTP {response.status_code}.")
        run = db.session.scalar(
            select(SeatingRun)
            .where(SeatingRun.created_by == f"background-job:{job.id}")
            .order_by(SeatingRun.id.desc())
        )
        if run is None:
            raise RuntimeError("Optimization completed without a persisted seating run.")
        update_progress(job.id, 95, 100, "Final validation passed")
        complete_job(job.id, {
            "seating_run_id": run.id,
            "solver_status": run.solver_status,
            "result_url": f"/reports/runs/{run.id}",
        })

    _run_job(job_id, operation)


@celery_app.task(name="seatplan.generate_report")
def generate_report_job(job_id):
    def operation(_app, job):
        from background_service import complete_job, update_progress
        from job_storage import put_bytes
        from reporting import build_excel_report, build_pdf_report, load_report_data

        run_id = int(job.payload["run_id"])
        report_format = job.payload["format"]
        update_progress(job.id, 15, 100, "Loading validated allocations")
        report = load_report_data(run_id)
        update_progress(job.id, 40, 100, f"Generating {report_format.upper()} report")
        if report_format == "pdf":
            output = build_pdf_report(report)
            content_type = "application/pdf"
        else:
            output = build_excel_report(report)
            content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"seating-run-{run_id}.{report_format}"
        artifact_key = f"reports/job-{job.id}/{filename}"
        put_bytes(artifact_key, output.getvalue())
        if not update_progress(job.id, 90, 100, "Report stored privately"):
            from job_storage import delete

            delete(artifact_key)
            return
        from security import record_audit

        record_audit(
            "seating_report_exported",
            "seating_run",
            run_id,
            after_values={"format": report_format, "background_job_id": job.id},
        )
        complete_job(job.id, {
            "artifact_key": artifact_key,
            "filename": filename,
            "content_type": content_type,
            "result_url": f"/jobs/{job.id}/download",
        })

    _run_job(job_id, operation)


TASK_NAMES = {
    "import_stage": "seatplan.stage_import",
    "import_commit": "seatplan.commit_import",
    "seating_optimization": "seatplan.optimize",
    "report_generation": "seatplan.generate_report",
}


def enqueue_job(job):
    if job.job_type not in TASK_NAMES:
        raise ValueError(f"Unsupported background job type: {job.job_type}")
    celery_app.send_task(TASK_NAMES[job.job_type], args=[job.id], task_id=job.task_id)
