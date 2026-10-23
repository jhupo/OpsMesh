"""Instance authorization always refreshes the current user."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.shared.errors import NotFoundError
from opsmesh.users.access import CurrentUser
from opsmesh.users.service import current_user
from opsmesh.workflows.instances.models import WorkflowInstance


def get_instance(
    session: Session, *, actor: CurrentUser, instance_id: UUID, lock: bool = False
) -> WorkflowInstance:
    actor = current_user(session, actor.user_id)
    query = select(WorkflowInstance).where(
        WorkflowInstance.id == instance_id,
        WorkflowInstance.owner_user_id == actor.user_id,
        WorkflowInstance.status != "deleted",
    )
    if lock:
        query = query.with_for_update()
    row = session.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise NotFoundError("The workflow instance does not exist")
    return row
