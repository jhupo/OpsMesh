"""Read-only audit APIs with current-user authorization."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from opsmesh.platform.audit.models import AuditEvent
from opsmesh.platform.audit.schemas import AuditEventPage, AuditEventView
from opsmesh.platform.audit.service import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    get_event,
    list_events,
)
from opsmesh.users.auth.dependencies import CurrentUserDep, DbSession

router = APIRouter(prefix="/platform/audit", tags=["platform"])


@router.get("", response_model=AuditEventPage, operation_id="platform.audit.list")
def list_audit_events(
    session: DbSession,
    reader: CurrentUserDep,
    after_sequence: Annotated[int | None, Query(ge=0)] = None,
    category: Annotated[str | None, Query(max_length=100)] = None,
    severity: Annotated[str | None, Query(max_length=20)] = None,
    actor_type: Annotated[str | None, Query(max_length=20)] = None,
    action: Annotated[str | None, Query(max_length=200)] = None,
    target_type: Annotated[str | None, Query(max_length=200)] = None,
    target_id: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)] = DEFAULT_LIST_LIMIT,
) -> AuditEventPage:
    events = list_events(
        session,
        reader=reader,
        after_sequence=after_sequence,
        category=category,
        severity=severity,
        actor_type=actor_type,
        action=action,
        target_type=target_type,
        target_id=target_id,
        limit=limit,
    )
    return AuditEventPage(
        events=[_event_view(event) for event in events],
        next_after_sequence=events[-1].sequence if len(events) == limit else None,
    )


@router.get("/{event_id}", response_model=AuditEventView, operation_id="platform.audit.read")
def get_audit_event(event_id: UUID, session: DbSession, reader: CurrentUserDep) -> AuditEventView:
    return _event_view(get_event(session, event_id, reader=reader))


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
        action=event.action,
        target_type=event.target_type,
        target_id=event.target_id,
        summary=event.summary,
        details=event.details,
        evidence_refs=list(event.evidence_refs),
        source=event.source,
    )
