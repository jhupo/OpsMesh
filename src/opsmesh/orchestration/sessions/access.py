"""Owner-only Session APIs; a configuration never grants access to another user's memory."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.orchestration.contracts import ExecutionScope, ExecutionSubject, ScopeAdmission
from opsmesh.orchestration.sessions.models import SessionRecord
from opsmesh.shared.errors import NotFoundError


def session_scope_of(*, configuration_id: UUID | None = None) -> ExecutionScope:
    """A personal Session optionally bound to a shared configuration source."""
    return ExecutionScope(configuration_id=configuration_id)


def record_scope(record: SessionRecord, *, principal_id: UUID) -> ExecutionScope:
    """Configuration reuse does not alter the Session's private user ownership."""
    return ExecutionScope(configuration_id=record.configuration_id)


def load_visible_session(
    session: Session,
    *,
    subject: ExecutionSubject,
    admission: ScopeAdmission,
    session_id: UUID,
    lock: bool = False,
) -> SessionRecord:
    """Load one session the subject may act on, or refuse.

    The row is locked on request so the caller's status checks and the
    checks of a concurrent close see one serialized order.
    """
    statement = select(SessionRecord).where(SessionRecord.id == session_id)
    if lock:
        statement = statement.with_for_update()
    record = session.scalar(statement)
    if record is None or record.principal_id != subject.user_id:
        raise NotFoundError(
            "The model session does not exist",
            details={"session_id": str(session_id)},
        )
    admission.admit(
        session, subject=subject, scope=record_scope(record, principal_id=subject.user_id)
    )
    return record
