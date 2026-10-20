"""Environment ownership and current scope checks."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.orchestration.contracts import (
    ExecutionScope,
    ExecutionSubject,
    ScopeAdmission,
    SharedResourceAdmission,
)
from opsmesh.orchestration.runtime.models import Environment
from opsmesh.shared.errors import NotFoundError


def environment_scope(record: Environment) -> ExecutionScope:
    if record.owner_ref.startswith("user:"):
        return ExecutionScope()
    return ExecutionScope(
        configuration_id=record.scope_id,
    )


def load_environment(
    session: Session,
    *,
    environment_id: UUID,
    subject: ExecutionSubject,
    admission: ScopeAdmission,
    lock: bool = False,
    shared_use: bool = False,
) -> Environment:
    query = select(Environment).where(Environment.id == environment_id)
    record = session.scalar(query.with_for_update() if lock else query)
    if record is None:
        raise NotFoundError("The environment does not exist")
    if record.principal_id != subject.user_id:
        if not shared_use or not isinstance(admission, SharedResourceAdmission):
            raise NotFoundError("The environment does not exist")
        admission.admit_resource_use(
            session,
            subject=subject,
            scope=environment_scope(record),
            kind="environment",
            resource_id=record.id,
            owner_user_id=record.principal_id,
        )
    admission.admit(session, subject=subject, scope=environment_scope(record))
    return record
