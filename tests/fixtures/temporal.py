from uuid import uuid4

from sqlalchemy.orm import Session

from opsmesh.identity.users.models import User
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember


def seed_workspace(session: Session) -> tuple[User, Workspace]:
    user = User(email=f"{uuid4()}@example.com", display_name="Temporal owner")
    workspace = Workspace(owner=user, name="Temporal", slug=uuid4().hex, settings={})
    session.add_all(
        [user, workspace, WorkspaceMember(workspace=workspace, user=user, role="owner")]
    )
    session.commit()
    return user, workspace


def durable_executions(session: Session, workspace_id, kind=None):
    """Inspect committed execution intents without impersonating a task queue."""
    from sqlalchemy import select

    from opsmesh.runtime.temporal.contracts import DispatchOperation, DispatchRequest
    from opsmesh.runtime.temporal.models import TemporalOutboxEntry

    records = session.scalars(
        select(TemporalOutboxEntry)
        .where(
            TemporalOutboxEntry.workspace_id == workspace_id,
        )
        .order_by(TemporalOutboxEntry.created_at, TemporalOutboxEntry.id)
    ).all()
    commands = [DispatchRequest.model_validate(record.payload) for record in records]
    return [
        command.execution
        for command in commands
        if command.operation == DispatchOperation.START
        and (kind is None or command.execution.kind == kind)
    ]
