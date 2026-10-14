"""Audit helpers shared by application blueprints."""

from __future__ import annotations

from typing import Any

from extensions import db
from models import AuditLog


def record_audit(
    action: str,
    entity_type: str,
    entity_id: str | int,
    *,
    before_values: dict[str, Any] | None = None,
    after_values: dict[str, Any] | None = None,
    actor: str | None = None,
) -> AuditLog:
    """Persist a security or business audit event without storing secrets."""
    resolved_actor = actor
    event = AuditLog(
        actor=(resolved_actor or "system")[:150],
        action=action[:80],
        entity_type=entity_type[:80],
        entity_id=str(entity_id)[:100],
        before_values=before_values,
        after_values=after_values,
    )
    db.session.add(event)
    db.session.commit()
    return event
