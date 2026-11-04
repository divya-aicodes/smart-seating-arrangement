"""Authentication, authorization, tenant isolation, and audit helpers."""

from __future__ import annotations

from functools import wraps
from typing import Any

from flask import abort, g, has_app_context, has_request_context, jsonify, request
from flask_login import current_user
from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

from extensions import db
from models import (
    AcademicYear,
    AuditLog,
    BackgroundJob,
    DataImport,
    Dataset,
    Department,
    Exam,
    ExamSession,
    Invigilator,
    Room,
    SeatingRun,
    Student,
    Subject,
)


TENANT_MODELS = (
    AcademicYear,
    BackgroundJob,
    DataImport,
    Dataset,
    Department,
    Exam,
    ExamSession,
    Invigilator,
    Room,
    SeatingRun,
    Student,
    Subject,
    AuditLog,
)


def is_api_request() -> bool:
    return request.path.startswith("/api/")


def security_error(code: str, message: str, status: int):
    if is_api_request():
        return jsonify({"error": {"code": code, "message": message}}), status
    abort(status, description=message)


def roles_required(*allowed_roles: str):
    allowed = set(allowed_roles)

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return security_error("authentication_required", "Authentication is required.", 401)
            if current_user.role not in allowed:
                return security_error("forbidden", "Your role cannot perform this action.", 403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


@event.listens_for(Session, "do_orm_execute")
def scope_tenant_selects(execute_state):
    """Add institution criteria to direct tenant-owned ORM queries in requests."""
    if (
        not execute_state.is_select
        or not has_app_context()
        or execute_state.execution_options.get("include_all_tenants")
    ):
        return
    institution_id = getattr(g, "institution_id", None)
    if institution_id is None:
        return
    statement = execute_state.statement
    for model in TENANT_MODELS:
        statement = statement.options(
            with_loader_criteria(
                model,
                model.institution_id == institution_id,
                include_aliases=True,
            )
        )
    execute_state.statement = statement


@event.listens_for(Session, "before_flush")
def scope_tenant_writes(session, _flush_context, _instances):
    """Stamp new tenant-owned records and reject cross-institution writes."""
    if not has_app_context():
        return
    institution_id = getattr(g, "institution_id", None)
    if institution_id is None:
        return
    for item in session.new.union(session.dirty):
        if not isinstance(item, TENANT_MODELS):
            continue
        item_institution = getattr(item, "institution_id", None)
        if item in session.new and item_institution is None:
            item.institution_id = institution_id
        elif item_institution != institution_id:
            raise PermissionError("Cross-institution database writes are not permitted.")


def record_audit(
    action: str,
    entity_type: str,
    entity_id: str | int,
    *,
    before_values: dict[str, Any] | None = None,
    after_values: dict[str, Any] | None = None,
    actor: str | None = None,
    institution_id: int | None = None,
    actor_user_id: int | None = None,
) -> AuditLog:
    """Persist a security or business audit event without storing secrets."""
    authenticated = has_request_context() and current_user.is_authenticated
    resolved_user_id = actor_user_id or (current_user.id if authenticated else None)
    if institution_id is not None:
        resolved_institution = institution_id
    elif authenticated:
        resolved_institution = current_user.institution_id
    elif has_request_context():
        resolved_institution = getattr(g, "institution_id", None)
    else:
        resolved_institution = None
    resolved_actor = actor or (
        f"{current_user.username} ({current_user.role})" if authenticated else "system"
    )
    event = AuditLog(
        institution_id=resolved_institution,
        actor_user_id=resolved_user_id,
        actor=resolved_actor[:150],
        action=action[:80],
        entity_type=entity_type[:80],
        entity_id=str(entity_id)[:100],
        before_values=before_values,
        after_values=after_values,
        request_id=getattr(g, "request_id", None) if has_request_context() else None,
        ip_address=(request.remote_addr or "")[:45] if has_request_context() else None,
    )
    db.session.add(event)
    db.session.commit()
    return event
