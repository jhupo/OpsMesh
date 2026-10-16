"""Security event service: record and query; never a second audit copy."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.governance.security_events.models import (
    MAX_KIND_LENGTH,
    MAX_SUMMARY_LENGTH,
    SEVERITIES,
    SecurityEvent,
)
from opsmesh.shared.errors import InvalidArgumentError, NotFoundError
from opsmesh.shared.pagination import PageParams


class SecurityEventLog:
    """The recorder identity.auth injects; facts join the caller's transaction.

    The caller owns the transaction boundary: a failed login records its fact
    in the fresh transaction the route opens for it, so the fact survives the
    rolled-back attempt.
    """

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
        if severity not in SEVERITIES:
            raise InvalidArgumentError("The security severity is unknown")
        if not kind or len(kind) > MAX_KIND_LENGTH:
            raise InvalidArgumentError("The security event kind is not usable")
        if len(summary) > MAX_SUMMARY_LENGTH:
            raise InvalidArgumentError("The security event summary is too long")
        session.add(
            SecurityEvent(
                kind=kind,
                severity=severity,
                summary=summary,
                details=details,
                user_id=user_id,
                occurred_at=datetime.now(UTC),
            )
        )


def get_event(session: Session, event_id: UUID) -> SecurityEvent:
    event = session.get(SecurityEvent, event_id)
    if event is None:
        raise NotFoundError("The security event does not exist")
    return event


def list_events(
    session: Session,
    *,
    page: PageParams,
    kind: str | None = None,
    severity: str | None = None,
    user_id: UUID | None = None,
) -> tuple[list[SecurityEvent], int]:
    """One offset page of security facts plus the matching total count."""
    if severity is not None and severity not in SEVERITIES:
        raise InvalidArgumentError("The security severity filter is unknown")
    query = select(SecurityEvent)
    count_query = select(func.count()).select_from(SecurityEvent)
    if kind is not None:
        query = query.where(SecurityEvent.kind == kind)
        count_query = count_query.where(SecurityEvent.kind == kind)
    if severity is not None:
        query = query.where(SecurityEvent.severity == severity)
        count_query = count_query.where(SecurityEvent.severity == severity)
    if user_id is not None:
        query = query.where(SecurityEvent.user_id == user_id)
        count_query = count_query.where(SecurityEvent.user_id == user_id)
    total = session.scalar(count_query) or 0
    rows = list(
        session.scalars(
            query.order_by(SecurityEvent.occurred_at.desc(), SecurityEvent.id.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
    )
    return rows, total
