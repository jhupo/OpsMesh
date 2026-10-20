"""Restricted extension append plus scope-aware audit query APIs.

The module never imports identity: the reader and the extension actor are
declared as placeholder dependencies that bootstrap replaces with the
assembled identity channels, and the scope admission is read from the
assembled application state. Until then every route answers 503 instead
of trusting an unauthenticated caller.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from opsmesh.platform.audit.models import AuditEvent
from opsmesh.platform.audit.schemas import (
    AuditAppendView,
    AuditEventPage,
    AuditEventView,
    ExtensionAuditAppend,
)
from opsmesh.platform.audit.service import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    AuditReader,
    AuditScopeAdmission,
    RejectingAuditScopeAdmission,
    authorize_audit_read,
    get_event,
    list_events,
    record_extension_event,
)
from opsmesh.shared.audit import AuditActor
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.http.dependencies import get_db_session

router = APIRouter(prefix="/platform/audit", tags=["platform"])

DbSession = Annotated[Session, Depends(get_db_session)]


def require_audit_reader() -> AuditReader:
    """Placeholder for the verified audit reader; bootstrap injects identity."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Audit reading is not assembled",
    )


def require_extension_actor() -> AuditActor:
    """Placeholder for the verified delegation actor; bootstrap injects it."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Extension audit append is not assembled",
    )


ReaderDep = Annotated[AuditReader, Depends(require_audit_reader)]
ExtensionActorDep = Annotated[AuditActor, Depends(require_extension_actor)]


def _scope_admission(request: Request) -> AuditScopeAdmission:
    admission = getattr(request.app.state, "audit_scope_admission", None)
    if isinstance(admission, AuditScopeAdmission):
        return admission
    return RejectingAuditScopeAdmission()


AdmissionDep = Annotated[AuditScopeAdmission, Depends(_scope_admission)]


@router.post(
    "",
    response_model=AuditAppendView,
    status_code=status.HTTP_201_CREATED,
    operation_id="platform.audit.record",
)
def record_event(
    payload: ExtensionAuditAppend,
    session: DbSession,
    actor: ExtensionActorDep,
) -> AuditAppendView:
    """Append one extension-reported fact under the verified invocation scope."""
    with session_transaction(session):
        receipt, replayed = record_extension_event(
            session,
            actor=actor,
            action=payload.action,
            target_type=payload.target_type,
            target_id=payload.target_id,
            summary=payload.summary,
            details=payload.details,
            evidence_refs=payload.evidence_refs,
            idempotency_key=payload.idempotency_key,
        )
    return AuditAppendView(event_id=receipt.event_id, replayed=replayed)


@router.get("", response_model=AuditEventPage, operation_id="platform.audit.list")
def list_audit_events(
    session: DbSession,
    reader: ReaderDep,
    admission: AdmissionDep,
    after_sequence: Annotated[int | None, Query(ge=0)] = None,
    category: Annotated[str | None, Query(max_length=100)] = None,
    severity: Annotated[str | None, Query(max_length=20)] = None,
    actor_type: Annotated[str | None, Query(max_length=20)] = None,
    action: Annotated[str | None, Query(max_length=200)] = None,
    target_type: Annotated[str | None, Query(max_length=200)] = None,
    target_id: Annotated[str | None, Query(max_length=200)] = None,
    configuration_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)] = DEFAULT_LIST_LIMIT,
) -> AuditEventPage:
    """One cursor page ordered by occurrence sequence; filters are ANDed."""
    authorize_audit_read(
        session,
        reader=reader,
        admission=admission,
        configuration_id=configuration_id,
    )
    events = list_events(
        session,
        after_sequence=after_sequence,
        category=category,
        severity=severity,
        actor_id=None if reader.has_platform_role("admin") else str(reader.user_id),
        actor_type=actor_type,
        action=action,
        target_type=target_type,
        target_id=target_id,
        configuration_id=configuration_id,
        limit=limit,
    )
    next_after_sequence = events[-1].sequence if len(events) == limit else None
    return AuditEventPage(
        events=[_event_view(event) for event in events],
        next_after_sequence=next_after_sequence,
    )


@router.get(
    "/{event_id}",
    response_model=AuditEventView,
    operation_id="platform.audit.read",
)
def get_audit_event(
    event_id: UUID,
    session: DbSession,
    reader: ReaderDep,
    admission: AdmissionDep,
) -> AuditEventView:
    event = get_event(session, event_id)
    authorize_audit_read(
        session,
        reader=reader,
        admission=admission,
        configuration_id=event.configuration_id,
    )
    if not reader.has_platform_role("admin") and event.actor_id != str(reader.user_id):
        from opsmesh.shared.errors import NotFoundError

        raise NotFoundError("The event does not exist")
    return _event_view(event)


def _event_view(event: AuditEvent) -> AuditEventView:
    return AuditEventView(
        event_id=event.id,
        sequence=event.sequence,
        occurred_at=event.occurred_at,
        recorded_at=event.recorded_at,
        category=event.category,
        severity=event.severity,
        schema_version=event.schema_version,
        operation_id=event.operation_id,
        correlation_id=event.correlation_id,
        causation_id=event.causation_id,
        trace_id=event.trace_id,
        actor_type=event.actor_type,
        actor_id=event.actor_id,
        configuration_id=event.configuration_id,
        installation_id=event.installation_id,
        instance_id=event.instance_id,
        invocation_id=event.invocation_id,
        package_key=event.package_key,
        configuration_revision=event.configuration_revision,
        action=event.action,
        target_type=event.target_type,
        target_id=event.target_id,
        summary=event.summary,
        details=event.details,
        evidence_refs=list(event.evidence_refs),
        source=event.source,
    )
