"""Current principal and instance ownership checks."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.execution.instances.models import AgentInstance
from opsmesh.shared.errors import NotFoundError
from opsmesh.users.access import CurrentUser
from opsmesh.users.service import current_user


def get_instance(
    session: Session, *, actor: CurrentUser, instance_id: UUID, lock: bool = False
) -> AgentInstance:
    current_user(session, actor.user_id)
    query = select(AgentInstance).where(
        AgentInstance.id == instance_id,
        AgentInstance.owner_user_id == actor.user_id,
        AgentInstance.status != "deleted",
    )
    if lock:
        query = query.with_for_update()
    row = session.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise NotFoundError("The Agent instance does not exist")
    return row
