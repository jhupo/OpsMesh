"""Administrator security event query APIs.

The reading subject is a placeholder dependency that bootstrap replaces with
the assembled identity channel; governance never imports identity.
"""

from __future__ import annotations

from typing import Annotated, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from opsmesh.governance.security_events import service
from opsmesh.governance.security_events.models import SEVERITIES, SecurityEvent
from opsmesh.governance.security_events.schemas import SecurityEventPage, SecurityEventView
from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.shared.http.dependencies import get_db_session
from opsmesh.shared.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, PageParams

router = APIRouter(prefix="/governance/security-events", tags=["governance"])

DbSession = Annotated[Session, Depends(get_db_session)]


class SecurityReader(Protocol):
    """The verified platform subject allowed to read security facts."""

    @property
    def user_id(self) -> UUID: ...


def require_security_reader() -> SecurityReader:
    """Placeholder for the platform reader; bootstrap injects identity."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Security event reading is not assembled",
    )


ReaderDep = Annotated[SecurityReader, Depends(require_security_reader)]


def _view(event: SecurityEvent) -> SecurityEventView:
    return SecurityEventView(
        event_id=event.id,
        kind=event.kind,
        severity=event.severity,
        summary=event.summary,
        details=event.details,
        user_id=event.user_id,
        occurred_at=event.occurred_at,
        created_at=event.created_at,
    )


@router.get(
    "", response_model=SecurityEventPage, operation_id="governance.security_events.list"
)
def list_security_events(
    session: DbSession,
    reader: ReaderDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
    kind: Annotated[str | None, Query(max_length=100)] = None,
    severity: Annotated[str | None, Query(max_length=20)] = None,
    user_id: Annotated[UUID | None, Query()] = None,
) -> SecurityEventPage:
    """One offset page of security facts; filters are ANDed server-side."""
    if severity is not None and severity not in SEVERITIES:
        raise InvalidArgumentError("The security severity filter is unknown")
    page = PageParams(limit=limit, offset=offset)
    events, total = service.list_events(
        session, page=page, kind=kind, severity=severity, user_id=user_id
    )
    return SecurityEventPage(events=[_view(event) for event in events], total=total)


@router.get(
    "/{event_id}",
    response_model=SecurityEventView,
    operation_id="governance.security_events.read",
)
def read_security_event(
    event_id: UUID, session: DbSession, reader: ReaderDep
) -> SecurityEventView:
    return _view(service.get_event(session, event_id))
