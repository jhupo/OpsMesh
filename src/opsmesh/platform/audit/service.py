"""Audit service: transactional idempotent append and authorized queries."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from opsmesh.platform.audit.models import AuditAppendReceipt, AuditEvent
from opsmesh.shared.audit import AuditActor, AuditRecord
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError, NotFoundError
from opsmesh.shared.security.redaction import (
    RedactionRules,
    redact_sensitive_payload,
    redact_sensitive_text,
    redaction_rules_for,
)
from opsmesh.shared.serialization import canonical_json, payload_digest

PLATFORM_ADMINISTRATOR = "admin"

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200


class AuditReader(Protocol):
    """The verified subject that reads audit facts.

    Platform roles travel with the subject exactly like the approval
    reader: the platform decides what an administrator may read without
    importing identity, and the configuration owner admits scoped access through
    the injected scope admission.
    """

    @property
    def user_id(self) -> UUID: ...

    def has_platform_role(self, role: str) -> bool: ...


@runtime_checkable
class AuditScopeAdmission(Protocol):
    """Whether one reader may read audit facts inside one named scope."""

    def admit(
        self,
        session: Session,
        *,
        user_id: UUID,
        configuration_id: UUID | None,
    ) -> None:
        """Raise a domain error when the scope is not admitted for the reader."""
        ...


class RejectingAuditScopeAdmission:
    """The default admission: every external scope is refused.

    Bootstrap injects configuration authorization; an unassembled scoped
    read fails closed.
    """

    def admit(
        self,
        session: Session,
        *,
        user_id: UUID,
        configuration_id: UUID | None,
    ) -> None:
        _ = session, user_id
        raise ForbiddenError(
            "The audit scope is not admitted",
            details={
                "configuration_id": str(configuration_id) if configuration_id else None,
            },
        )


def authorize_audit_read(
    session: Session,
    *,
    reader: AuditReader,
    admission: AuditScopeAdmission,
    configuration_id: UUID | None,
) -> None:
    """Authorize one audit read: administrators read every scope, everyone
    else is admitted inside a named scope by its owner.

    Unscoped reads require an administrator or an explicitly admitted delegation.
    Non-administrator results always remain restricted to the current actor.
    """
    if reader.has_platform_role(PLATFORM_ADMINISTRATOR):
        return
    admission.admit(
        session,
        user_id=reader.user_id,
        configuration_id=configuration_id,
    )


def append_platform_event(
    session: Session,
    *,
    actor: AuditActor,
    action: str,
    target_type: str,
    target_id: str,
    summary: str,
    details: dict[str, object] | None = None,
) -> AuditEvent:
    """Append one platform fact inside the caller's transaction."""
    event = AuditRecord(
        category="operation",
        action=action,
        actor=actor,
        source="platform",
        target_type=target_type,
        target_id=target_id,
        summary=summary,
        payload_json=canonical_json(details or {}),
    )
    append_audit_record(session, event, rules=redaction_rules_for(session))
    session.flush()
    return get_event(session, event.event_id)


def record_extension_event(
    session: Session,
    *,
    actor: AuditActor,
    action: str,
    target_type: str,
    target_id: str,
    summary: str,
    details: dict[str, object] | None,
    evidence_refs: list[str],
    idempotency_key: str,
) -> tuple[AuditAppendReceipt, bool]:
    """Append one extension-reported fact; identical retries reuse the receipt.

    Returns the receipt and whether this call replayed an earlier append. A
    reused key with a different declared input is a conflict, never a silent
    second event.
    """
    if actor.installation_id is None or actor.invocation_id is None:
        raise InvalidArgumentError("An extension append requires its invocation scope")
    declared = {
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "summary": summary,
        "details": details or {},
        "evidence_refs": list(evidence_refs),
    }
    scope = f"installation:{actor.installation_id}:invocation:{actor.invocation_id}"
    input_digest = payload_digest(declared)
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"audit-receipt:{scope}:{idempotency_key}"},
    )
    session.flush()
    existing = session.scalar(
        select(AuditAppendReceipt).where(
            AuditAppendReceipt.idempotency_scope == scope,
            AuditAppendReceipt.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.input_digest != input_digest:
            raise ConflictError("The idempotency key was reused with a different audit input")
        return existing, True
    envelope = AuditRecord(
        category="operation",
        action=action,
        actor=actor,
        source="extension_reported",
        target_type=target_type,
        target_id=target_id,
        summary=summary,
        payload_json=canonical_json(details or {}),
        evidence_refs=tuple(evidence_refs),
    )
    append_audit_record(session, envelope, rules=redaction_rules_for(session))
    session.flush()
    event = get_event(session, envelope.event_id)
    receipt = AuditAppendReceipt(
        idempotency_scope=scope,
        idempotency_key=idempotency_key,
        input_digest=input_digest,
        event_id=event.id,
    )
    session.add(receipt)
    return receipt, False


def get_event(session: Session, event_id: UUID) -> AuditEvent:
    event = session.get(AuditEvent, event_id)
    if event is None:
        raise NotFoundError("The audit event does not exist")
    return event


def list_events(
    session: Session,
    *,
    after_sequence: int | None = None,
    actor_id: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    actor_type: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    configuration_id: UUID | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> list[AuditEvent]:
    """Cursor pagination by database-assigned occurrence sequence; filters are ANDed server-side."""
    if limit < 1 or limit > MAX_LIST_LIMIT:
        raise InvalidArgumentError(f"The audit page limit must be between 1 and {MAX_LIST_LIMIT}")
    query = select(AuditEvent).order_by(AuditEvent.sequence.asc())
    if after_sequence is not None:
        query = query.where(AuditEvent.sequence > after_sequence)
    if actor_id is not None:
        query = query.where(AuditEvent.actor_id == actor_id)
    if category is not None:
        query = query.where(AuditEvent.category == category)
    if severity is not None:
        query = query.where(AuditEvent.severity == severity)
    if actor_type is not None:
        query = query.where(AuditEvent.actor_type == actor_type)
    if action is not None:
        query = query.where(AuditEvent.action == action)
    if target_type is not None:
        query = query.where(AuditEvent.target_type == target_type)
    if target_id is not None:
        query = query.where(AuditEvent.target_id == target_id)
    if configuration_id is not None:
        query = query.where(AuditEvent.configuration_id == configuration_id)
    return list(session.scalars(query.limit(limit)))


def _append_event(session: Session, *, envelope: AuditRecord) -> AuditEvent:
    """Append one immutable occurrence without serializing unrelated writers."""
    actor = envelope.actor
    event = AuditEvent(
        id=envelope.event_id,
        occurred_at=envelope.occurred_at,
        recorded_at=datetime.now(UTC),
        category=envelope.category,
        severity=envelope.severity,
        schema_version=envelope.schema_version,
        operation_id=envelope.operation_id,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        trace_id=envelope.trace_id,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        configuration_id=actor.configuration_id,
        installation_id=actor.installation_id,
        instance_id=actor.instance_id,
        invocation_id=actor.invocation_id,
        package_key=actor.package_key,
        configuration_revision=actor.configuration_revision,
        action=envelope.action,
        target_type=envelope.target_type or "event",
        target_id=envelope.target_id or str(envelope.event_id),
        summary=envelope.summary,
        details=envelope.payload,
        evidence_refs=list(envelope.evidence_refs),
        source=envelope.source,
    )
    session.add(event)
    return event


def append_audit_record(
    session: Session, record: AuditRecord, *, rules: RedactionRules
) -> AuditEvent:
    """Redact and append one audit record in the caller's transaction, without commit."""
    event = replace(
        record,
        summary=redact_sensitive_text(record.summary, rules=rules),
        payload_json=canonical_json(redact_sensitive_payload(record.payload, rules=rules)),
    )
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"audit-event:{event.event_id}"},
    )
    session.flush()
    existing = session.get(AuditEvent, event.event_id)
    if existing is not None:
        actual = {
            "actor": {
                key: getattr(existing, key)
                for key in (
                    "actor_type",
                    "actor_id",
                    "configuration_id",
                    "installation_id",
                    "instance_id",
                    "invocation_id",
                    "package_key",
                    "configuration_revision",
                )
            },
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
    if event.source not in {"platform", "extension_reported"}:
        raise InvalidArgumentError("Unsupported event origin")
    return _append_event(session, envelope=event)


class SecurityAuditRecorder:
    """Persist authentication facts directly, including separately committed failures."""

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
            rules=redaction_rules_for(session),
        )
