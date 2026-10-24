"""Authorization for independently owned persistent environments."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.execution.runtime.models import SandboxEnvironment
from opsmesh.shared.errors import NotFoundError
from opsmesh.users.access import CurrentUser
from opsmesh.users.service import current_user


def get_environment(
    session: Session, *, actor: CurrentUser, environment_id: UUID, lock: bool = False
) -> SandboxEnvironment:
    current_user(session, actor.user_id)
    query = select(SandboxEnvironment).where(
        SandboxEnvironment.id == environment_id,
        SandboxEnvironment.owner_user_id == actor.user_id,
    )
    if lock:
        query = query.with_for_update()
    row = session.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise NotFoundError("The sandbox environment does not exist")
    return row
