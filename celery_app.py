"""Celery worker entrypoint. PostgreSQL remains the authoritative job store."""

from celery import Celery

from config import load_app_config


settings = load_app_config()
broker_url = str(settings.get("CELERY_BROKER_URL") or "redis://localhost:6379/0")
result_backend = str(settings.get("CELERY_RESULT_BACKEND") or "") or None

celery_app = Celery(
    "seatplan",
    broker=broker_url,
    backend=result_backend,
    include=["background_tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    task_track_started=False,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=1800,
    task_time_limit=1860,
    broker_transport_options={"visibility_timeout": 3600},
    timezone="UTC",
    enable_utc=True,
)
