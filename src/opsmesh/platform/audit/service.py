"""Transactional audit writes and user-scoped, cursor-based reads."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from opsmesh.platform.audit.contracts import AuditActor, AuditRecord
from opsmesh.platform.audit.models import AuditEvent
from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError
from opsmesh.shared.security.redaction import (
    RedactionRules,
    redact_sensitive_payload,
    redact_sensitive_text,
    require_redaction_rules,
)
from opsmesh.shared.serialization import canonical_json
from opsmesh.users.access import CurrentUser, ensure_active_user

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200


def get_event(session: Session, event_id: UUID, *, reader: CurrentUser) -> AuditEvent:
    ensure_active_user(reader)
    event = session.get(AuditEvent, event_id)
    if event is None or (
        not reader.has_platform_role("admin") and event.actor_id != str(reader.user_id)
    ):
        raise NotFoundError("The audit event does not exist")
    return event


def list_events(
    session: Session,
    *,
    reader: CurrentUser,
    after_sequence: int | None = None,
    category: str | None = None,
    severity: str | None = None,
    actor_type: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> list[AuditEvent]:
    """Administrators read all facts; other users only read their own attribution."""
    ensure_active_user(reader)
    if limit < 1 or limit > MAX_LIST_LIMIT:
        raise InvalidArgumentError(f"The audit page limit must be between 1 and {MAX_LIST_LIMIT}")
    if after_sequence is not None and after_sequence < 0:
        raise InvalidArgumentError("The audit cursor must be non-negative")
    query = select(AuditEvent).order_by(AuditEvent.sequence.asc())
    if not reader.has_platform_role("admin"):
        query = query.where(AuditEvent.actor_id == str(reader.user_id))
    if after_sequence is not None:
        query = query.where(AuditEvent.sequence > after_sequence)
    for field, value in (
        (AuditEvent.category, category),
        (AuditEvent.severity, severity),
        (AuditEvent.actor_type, actor_type),
        (AuditEvent.action, action),
        (AuditEvent.target_type, target_type),
        (AuditEvent.target_id, target_id),
    ):
        if value is not None:
            query = query.where(field == value)
    return list(session.scalars(query.limit(limit)))


def append_audit_record(
    session: Session, record: AuditRecord, *, rules: RedactionRules
) -> AuditEvent:
    """Redact and append one occurrence in the caller's transaction, without commit."""
    event = replace(
        record,
        summary=redact_sensitive_text(record.summary, rules=rules),
        payload_json=canonical_json(redact_sensitive_payload(record.payload, rules=rules)),
    )
    if event.source != "platform":
        raise InvalidArgumentError("Unsupported audit origin")
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"audit-event:{event.event_id}"},
    )
    session.flush()
    existing = session.get(AuditEvent, event.event_id)
    if existing is not None:
        actual = {
            "actor": {key: getattr(existing, key) for key in asdict(event.actor)},
            "category": existing.category,
            "action": existing.action,
            "source": existing.source,
            "target_type": existing.target_type,
            "target_id": existing.target_id,
            "summary": existing.summary,
            "payload": existing.details,
            "severity": existing.severity,
            "schema_version": existing.schema_version,
            "occurred_at": existing.occurred_at,
            "operation_id": existing.operation_id,
            "correlation_id": existing.correlation_id,
            "causation_id": existing.causation_id,
            "trace_id": existing.trace_id,
            "evidence_refs": tuple(existing.evidence_refs),
        }
        declared = {key: getattr(event, key) for key in actual}
        declared["actor"] = asdict(event.actor)
        declared["target_type"] = event.target_type or "event"
        declared["target_id"] = event.target_id or str(event.event_id)
        if actual != declared:
            raise ConflictError("Event occurrence ID was reused with different content")
        return existing
    stored = AuditEvent(
        id=event.event_id,
        occurred_at=event.occurred_at,
        recorded_at=datetime.now(UTC),
        category=event.category,
        severity=event.severity,
        schema_version=event.schema_version,
        operation_id=event.operation_id,
        correlation_id=event.correlation_id,
        causation_id=event.causation_id,
        trace_id=event.trace_id,
        **asdict(event.actor),
        action=event.action,
        target_type=event.target_type or "event",
        target_id=event.target_id or str(event.event_id),
        summary=event.summary,
        details=event.payload,
        evidence_refs=list(event.evidence_refs),
        source=event.source,
    )
    session.add(stored)
    return stored


class SecurityAuditRecorder:
    """Persist authentication facts, including separately committed failures."""

    def record(
        self,
        session: Session,
        *,
        kind: str,
        severity: str,
        summary: str,
        details: dict[str, object],
        user_id: UUID | None,
    ) -> None:
        if severity not in {"info", "warning", "critical"}:
            raise InvalidArgumentError("Unknown security severity")
        append_audit_record(
            session,
            AuditRecord(
                category="security",
                action=kind,
                source="platform",
                actor=AuditActor("user", str(user_id) if user_id else None),
                target_type="user",
                target_id=str(user_id) if user_id else "anonymous",
                summary=summary,
                payload_json=canonical_json(details),
                severity=severity,
            ),
            rules=require_redaction_rules(session.info.get("redaction_rules")),
        )
