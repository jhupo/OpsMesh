"""Restricted extension append plus admin audit query and verification APIs.

The module never imports identity: the reader and the extension actor are
declared as placeholder dependencies that bootstrap replaces with the
assembled identity channels. Until then every route answers 503 instead of
trusting an unauthenticated caller.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from opsmesh.governance.audit.models import AuditEvent, AuditVerification
from opsmesh.governance.audit.schemas import (
    AuditAppendView,
    AuditEventPage,
    AuditEventView,
    AuditVerificationRequest,
    AuditVerificationView,
    ExtensionAuditAppend,
)
from opsmesh.governance.audit.service import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    AuditActor,
    AuditSubject,
    get_event,
    get_verification,
    list_events,
    record_extension_event,
    start_verification,
)
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.http.dependencies import get_db_session

router = APIRouter(prefix="/governance/audit", tags=["governance"])

DbSession = Annotated[Session, Depends(get_db_session)]


def require_audit_reader() -> AuditSubject:
    """Placeholder for the platform audit reader; bootstrap injects identity."""
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


ReaderDep = Annotated[AuditSubject, Depends(require_audit_reader)]
ExtensionActorDep = Annotated[AuditActor, Depends(require_extension_actor)]


@router.post(
    "/events",
    response_model=AuditAppendView,
    status_code=status.HTTP_201_CREATED,
    operation_id="governance.audit.events.record",
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


@router.get("/events", response_model=AuditEventPage, operation_id="governance.audit.events.list")
def list_audit_events(
    session: DbSession,
    reader: ReaderDep,
    after_sequence: Annotated[int | None, Query(ge=0)] = None,
    actor_type: Annotated[str | None, Query(max_length=20)] = None,
    action: Annotated[str | None, Query(max_length=200)] = None,
    target_type: Annotated[str | None, Query(max_length=200)] = None,
    target_id: Annotated[str | None, Query(max_length=200)] = None,
    workspace_id: Annotated[UUID | None, Query()] = None,
    project_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)] = DEFAULT_LIST_LIMIT,
) -> AuditEventPage:
    """One cursor page ordered by chain sequence; filters are ANDed."""
    events = list_events(
        session,
        after_sequence=after_sequence,
        actor_type=actor_type,
        action=action,
        target_type=target_type,
        target_id=target_id,
        workspace_id=workspace_id,
        project_id=project_id,
        limit=limit,
    )
    next_after_sequence = events[-1].sequence if len(events) == limit else None
    return AuditEventPage(
        events=[_event_view(event) for event in events],
        next_after_sequence=next_after_sequence,
    )


@router.get(
    "/events/{event_id}",
    response_model=AuditEventView,
    operation_id="governance.audit.events.read",
)
def get_audit_event(event_id: UUID, session: DbSession, reader: ReaderDep) -> AuditEventView:
    return _event_view(get_event(session, event_id))


@router.post(
    "/verifications",
    response_model=AuditVerificationView,
    status_code=status.HTTP_201_CREATED,
    operation_id="governance.audit.verifications.create",
)
def create_verification(
    payload: AuditVerificationRequest,
    session: DbSession,
    reader: ReaderDep,
) -> AuditVerificationView:
    """Register one verification run and stage its durable execution."""
    with session_transaction(session):
        verification = start_verification(
            session,
            requested_by=reader,
            from_sequence=payload.from_sequence,
            to_sequence=payload.to_sequence,
        )
    return _verification_view(verification)


@router.get(
    "/verifications/{verification_id}",
    response_model=AuditVerificationView,
    operation_id="governance.audit.verifications.read",
)
def get_audit_verification(
    verification_id: UUID, session: DbSession, reader: ReaderDep
) -> AuditVerificationView:
    return _verification_view(get_verification(session, verification_id))


def _event_view(event: AuditEvent) -> AuditEventView:
    return AuditEventView(
        event_id=event.id,
        sequence=event.sequence,
        occurred_at=event.occurred_at,
        actor_type=event.actor_type,
        actor_id=event.actor_id,
        workspace_id=event.workspace_id,
        project_id=event.project_id,
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
        previous_hash=event.previous_hash,
        event_hash=event.event_hash,
    )


def _verification_view(verification: AuditVerification) -> AuditVerificationView:
    return AuditVerificationView(
        verification_id=verification.id,
        status=verification.status,
        from_sequence=verification.from_sequence,
        to_sequence=verification.to_sequence,
        checked_count=verification.checked_count,
        first_broken_sequence=verification.first_broken_sequence,
        failure_reason=verification.failure_reason,
        created_at=verification.created_at,
    )
