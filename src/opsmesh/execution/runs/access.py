"""Run ownership and current principal checks."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.execution.instances.access import current_user
from opsmesh.execution.runs.models import ExecutionRun
from opsmesh.shared.errors import ForbiddenError, NotFoundError
from opsmesh.users.access import CurrentUser


def get_run(
    session: Session, *, actor: CurrentUser, run_id: UUID, lock: bool = False
) -> ExecutionRun:
    current_user(session, actor.user_id)
    query = select(ExecutionRun).where(
        ExecutionRun.id == run_id, ExecutionRun.user_id == actor.user_id
    )
    if lock:
        query = query.with_for_update()
    row = session.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise NotFoundError("The run does not exist")
    return row


def authorize_run(session: Session, run_id: UUID, *, lock: bool = False) -> ExecutionRun:
    row = session.get(ExecutionRun, run_id, populate_existing=True)
    if row is None:
        raise NotFoundError("The run does not exist")
    actor = current_user(session, row.user_id)
    row = get_run(session, actor=actor, run_id=run_id, lock=lock)
    if row.status != "running":
        raise ForbiddenError("The run is not active")
    return row
