"""Validated application configuration and structured logging.

The module keeps environment parsing independent from Flask application setup so
configuration can be tested without creating a second application instance.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from logging.config import dictConfig
from typing import Mapping
from urllib.parse import urlsplit

from flask import Flask, g, has_request_context


MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 256 * 1024 * 1024
MAX_STUDENTS = 5_000
DEFAULT_IMPORT_CHUNK_SIZE = 5_000


def _env_flag(
    name: str,
    default: bool = False,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    values = environ if environ is not None else os.environ
    value = values.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(
    name: str,
    default: int,
    minimum: int,
    maximum: int,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    values = environ if environ is not None else os.environ
    raw_value = values.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value.strip())
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a whole number.") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}.")
    return value


def _parse_cors_origins(raw_value: str) -> set[str]:
    """Parse exact HTTP(S) origins and reject wildcard or path-based entries."""
    origins: set[str] = set()
    for raw_origin in raw_value.split(","):
        origin = raw_origin.strip().rstrip("/")
        if not origin:
            continue
        parsed = urlsplit(origin)
        if (
            "*" in origin
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise RuntimeError(
                "CORS_ALLOWED_ORIGINS must contain exact HTTP(S) origins without "
                "wildcards, credentials, paths, queries, or fragments."
            )
        origins.add(origin)
    return origins


def _parse_allowed_hosts(raw_value: str) -> set[str]:
    """Parse hostnames used to defend production Host-header handling."""
    hosts: set[str] = set()
    for raw_host in raw_value.split(","):
        host = raw_host.strip().casefold()
        if not host:
            continue
        if (
            "*" in host
            or "://" in host
            or "/" in host
            or "?" in host
            or "#" in host
            or any(char.isspace() for char in host)
        ):
            raise RuntimeError(
                "ALLOWED_HOSTS must contain exact hostnames without schemes, ports, "
                "wildcards, paths, queries, or fragments."
            )
        hosts.add(host)
    return hosts


def _database_url(raw_value: str | None, *, is_production: bool) -> str:
    if is_production and not raw_value:
        raise RuntimeError("DATABASE_URL must be configured when APP_ENV=production.")
    database_url = (raw_value or "sqlite:///smart_seating.db").strip()
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if is_production and not database_url.startswith("postgresql+psycopg://"):
        raise RuntimeError("Production deployments must use PostgreSQL through Psycopg 3.")
    return database_url


def load_app_config(environ: Mapping[str, str] | None = None) -> dict[str, object]:
    """Build and validate the complete runtime configuration from environment values."""
    values = environ if environ is not None else os.environ
    is_production = values.get("APP_ENV", "development").strip().lower() == "production"
    configured_secret = values.get("SECRET_KEY")
    if is_production and (
        not configured_secret
        or configured_secret == "[REDACTED_SECRET]"
        or len(configured_secret) < 32
    ):
        raise RuntimeError(
            "SECRET_KEY must be a non-placeholder value of at least 32 characters in production."
        )

    session_cookie_secure = _env_flag(
        "SESSION_COOKIE_SECURE", is_production, environ=values
    )
    session_cookie_samesite = values.get("SESSION_COOKIE_SAMESITE", "Lax").strip().title()
    if session_cookie_samesite not in {"Lax", "Strict", "None"}:
        raise RuntimeError("SESSION_COOKIE_SAMESITE must be Lax, Strict, or None.")
    if is_production and not session_cookie_secure:
        raise RuntimeError("SESSION_COOKIE_SECURE cannot be disabled in production.")

    cors_origins = _parse_cors_origins(values.get("CORS_ALLOWED_ORIGINS", ""))
    cors_allow_credentials = _env_flag(
        "CORS_ALLOW_CREDENTIALS", False, environ=values
    )
    if cors_allow_credentials and (
        not session_cookie_secure or session_cookie_samesite != "None"
    ):
        raise RuntimeError(
            "Cross-origin credentialed sessions require SESSION_COOKIE_SECURE=true and "
            "SESSION_COOKIE_SAMESITE=None."
        )

    allowed_hosts = _parse_allowed_hosts(values.get("ALLOWED_HOSTS", ""))
    if is_production and not allowed_hosts:
        raise RuntimeError("ALLOWED_HOSTS must contain at least one public hostname in production.")

    database_url = _database_url(
        values.get("DATABASE_URL"), is_production=is_production
    )
    redis_url = values.get("REDIS_URL", "").strip()
    celery_broker_url = values.get("CELERY_BROKER_URL", redis_url).strip()
    background_jobs_enabled = _env_flag(
        "BACKGROUND_JOBS_ENABLED", False, environ=values
    )
    if background_jobs_enabled and not celery_broker_url:
        raise RuntimeError(
            "CELERY_BROKER_URL or REDIS_URL is required when background jobs are enabled."
        )
    storage_backend = values.get("JOB_STORAGE_BACKEND", "local").strip().lower()
    if storage_backend not in {"local", "s3"}:
        raise RuntimeError("JOB_STORAGE_BACKEND must be local or s3.")
    if storage_backend == "s3" and not values.get("S3_BUCKET", "").strip():
        raise RuntimeError("S3_BUCKET is required when JOB_STORAGE_BACKEND=s3.")
    engine_options: dict[str, object] = {"pool_pre_ping": True}
    if database_url.startswith("postgresql+psycopg://"):
        engine_options.update({"pool_recycle": 300, "pool_size": 5, "max_overflow": 10})

    log_level = values.get("LOG_LEVEL", "INFO").strip().upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise RuntimeError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL.")

    return {
        "APP_ENV": "production" if is_production else "development",
        "IS_PRODUCTION": is_production,
        "SECRET_KEY": configured_secret or secrets.token_urlsafe(32),
        "MAX_CONTENT_LENGTH": MAX_REQUEST_BYTES,
        "IMPORT_CHUNK_SIZE": _env_int(
            "IMPORT_CHUNK_SIZE",
            DEFAULT_IMPORT_CHUNK_SIZE,
            100,
            20_000,
            environ=values,
        ),
        "BACKGROUND_JOBS_ENABLED": background_jobs_enabled,
        "CELERY_BROKER_URL": celery_broker_url,
        "CELERY_RESULT_BACKEND": values.get("CELERY_RESULT_BACKEND", "").strip(),
        "BACKGROUND_IMPORT_MIN_BYTES": _env_int(
            "BACKGROUND_IMPORT_MIN_BYTES", 512 * 1024, 1, MAX_REQUEST_BYTES, environ=values
        ),
        "BACKGROUND_IMPORT_COMMIT_MIN_ROWS": _env_int(
            "BACKGROUND_IMPORT_COMMIT_MIN_ROWS", 10_000, 1, 500_000, environ=values
        ),
        "BACKGROUND_OPTIMIZATION_MIN_STUDENTS": _env_int(
            "BACKGROUND_OPTIMIZATION_MIN_STUDENTS", 500, 1, MAX_STUDENTS, environ=values
        ),
        "BACKGROUND_REPORT_MIN_ALLOCATIONS": _env_int(
            "BACKGROUND_REPORT_MIN_ALLOCATIONS", 1_000, 1, 500_000, environ=values
        ),
        "JOB_STORAGE_BACKEND": storage_backend,
        "JOB_STORAGE_DIR": values.get("JOB_STORAGE_DIR", "instance/job-storage").strip(),
        "S3_BUCKET": values.get("S3_BUCKET", "").strip(),
        "S3_PREFIX": values.get("S3_PREFIX", "seatplan-jobs").strip().strip("/"),
        "S3_ENDPOINT_URL": values.get("S3_ENDPOINT_URL", "").strip() or None,
        "S3_REGION": values.get("S3_REGION", "").strip() or None,
        "SESSION_COOKIE_NAME": "smart_seating_session",
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": session_cookie_samesite,
        "SESSION_COOKIE_SECURE": session_cookie_secure,
        "SESSION_COOKIE_DOMAIN": None,
        "PERMANENT_SESSION_LIFETIME": timedelta(hours=8),
        "WTF_CSRF_ENABLED": True,
        "WTF_CSRF_TIME_LIMIT": 7200,
        "WTF_CSRF_SSL_STRICT": is_production,
        "CORS_ALLOWED_ORIGINS": cors_origins,
        "CORS_ALLOW_CREDENTIALS": cors_allow_credentials,
        "ALLOWED_HOSTS": allowed_hosts,
        "PREFERRED_URL_SCHEME": "https" if is_production else "http",
        "SQLALCHEMY_DATABASE_URI": database_url,
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "SQLALCHEMY_ENGINE_OPTIONS": engine_options,
        "TRUST_PROXY_HEADERS": _env_flag(
            "TRUST_PROXY_HEADERS", False, environ=values
        ),
        "INITIAL_SETUP_TOKEN": values.get("INITIAL_SETUP_TOKEN", "").strip(),
        "RATELIMIT_STORAGE_URI": values.get(
            "RATE_LIMIT_STORAGE_URI", redis_url or "memory://"
        ).strip(),
        "RATELIMIT_DEFAULT": values.get(
            "RATE_LIMIT_DEFAULT", "300 per minute"
        ).strip(),
        "RATELIMIT_HEADERS_ENABLED": True,
        "LOG_LEVEL": log_level,
    }


class JsonLogFormatter(logging.Formatter):
    """Emit one JSON object per application log event."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class RequestContextFilter(logging.Filter):
    """Attach the current request identifier without logging request data or secrets."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = getattr(g, "request_id", None) if has_request_context() else None
        return True


def configure_logging(app: Flask) -> None:
    """Configure deterministic structured logs for Flask and application modules."""
    dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"request_context": {"()": RequestContextFilter}},
        "formatters": {"json": {"()": JsonLogFormatter}},
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
                "formatter": "json",
                "filters": ["request_context"],
            }
        },
        "root": {
            "level": str(app.config["LOG_LEVEL"]),
            "handlers": ["console"],
        },
    })
    app.logger.handlers.clear()
    app.logger.propagate = True
    app.logger.setLevel(str(app.config["LOG_LEVEL"]))
