"""Transactional lifecycle operations for durable background jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import BackgroundJob


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def create_job(job_type, payload, *, idempotency_key=None, created_by=None):
    if idempotency_key:
        existing = db.session.scalar(
            select(BackgroundJob).where(BackgroundJob.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return existing, True
    job = BackgroundJob(
        task_id=str(uuid4()),
        idempotency_key=idempotency_key,
        job_type=job_type,
        status="queued",
        payload=payload,
        progress_current=0,
        progress_total=max(1, int(payload.get("progress_total", 100))),
        progress_message="Queued",
        created_by=created_by,
    )
    try:
        db.session.add(job)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        if idempotency_key:
            existing = db.session.scalar(
                select(BackgroundJob).where(BackgroundJob.idempotency_key == idempotency_key)
            )
            if existing is not None:
                return existing, True
        raise
    return job, False


def claim_job(job_id):
    now = datetime.now(timezone.utc)
    claimed = db.session.execute(
        update(BackgroundJob)
        .where(BackgroundJob.id == job_id, BackgroundJob.status == "queued")
        .values(
            status="running",
            started_at=now,
            attempts=BackgroundJob.attempts + 1,
            progress_message="Starting",
        )
    ).rowcount
    db.session.commit()
    return db.session.get(BackgroundJob, job_id) if claimed else None


def update_progress(job_id, current, total, message):
    # Use an independent short transaction so progress persistence never commits
    # or rolls back the domain transaction currently owned by an import task.
    with db.engine.begin() as connection:
        state = connection.execute(
            select(BackgroundJob.status, BackgroundJob.cancellation_requested)
            .where(BackgroundJob.id == job_id)
        ).one_or_none()
        if state is None or state.status != "running":
            return False
        if state.cancellation_requested:
            connection.execute(
                update(BackgroundJob)
                .where(BackgroundJob.id == job_id, BackgroundJob.status == "running")
                .values(
                    status="cancelled",
                    cancelled_at=datetime.now(timezone.utc),
                    progress_message="Cancelled safely",
                )
            )
            return False
        bounded_total = max(1, int(total))
        connection.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == job_id, BackgroundJob.status == "running")
            .values(
                progress_total=bounded_total,
                progress_current=min(max(0, int(current)), bounded_total),
                progress_message=str(message)[:250],
            )
        )
    return True


def complete_job(job_id, result):
    job = db.session.get(BackgroundJob, job_id)
    if job is None or job.status not in {"running", "queued"}:
        return job
    # Cancellation is honored only at explicit progress checkpoints. Once the
    # operation has a complete result, publishing it is safer than discarding
    # it after database or artifact side effects may already have committed.
    job.status = "completed"
    job.result = result
    job.progress_current = job.progress_total
    job.progress_message = "Completed"
    job.completed_at = datetime.now(timezone.utc)
    db.session.commit()
    return job


def fail_job(job_id, message):
    job = db.session.get(BackgroundJob, job_id)
    if job is not None and job.status not in TERMINAL_STATUSES:
        job.status = "failed"
        job.error_message = str(message)[:2000]
        job.progress_message = "Failed"
        job.completed_at = datetime.now(timezone.utc)
        db.session.commit()
    return job


def request_cancellation(job_id):
    job = db.session.get(BackgroundJob, job_id)
    if job is None or job.status in TERMINAL_STATUSES:
        return job
    job.cancellation_requested = True
    if job.status == "queued":
        job.status = "cancelled"
        job.cancelled_at = datetime.now(timezone.utc)
        job.progress_message = "Cancelled before execution"
    db.session.commit()
    return job


def reset_for_retry(job_id):
    job = db.session.get(BackgroundJob, job_id)
    if job is None or job.status not in {"failed", "cancelled"}:
        return None
    job.task_id = str(uuid4())
    job.status = "queued"
    job.progress_current = 0
    job.progress_message = "Queued for retry"
    job.error_message = None
    job.result = None
    job.cancellation_requested = False
    job.started_at = None
    job.completed_at = None
    job.cancelled_at = None
    db.session.commit()
    return job


def fail_stale_jobs(cutoff):
    """Mark abandoned running jobs failed so an operator can retry them safely."""
    jobs = db.session.scalars(
        select(BackgroundJob).where(
            BackgroundJob.status == "running",
            BackgroundJob.started_at < cutoff,
        )
    ).all()
    now = datetime.now(timezone.utc)
    for job in jobs:
        job.status = "failed"
        job.error_message = "Worker heartbeat was lost; review and retry this job safely."
        job.progress_message = "Worker lost"
        job.completed_at = now
    db.session.commit()
    return len(jobs)
