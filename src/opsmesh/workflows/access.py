"""Current-user checks and separate draft, published-read and use permissions."""

from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from opsmesh.shared.errors import ConflictError, ForbiddenError, NotFoundError
from opsmesh.users.access import CurrentUser
from opsmesh.users.service import current_user
from opsmesh.workflows.contracts import Permission
from opsmesh.workflows.models import WorkflowDefinition, WorkflowGrant, WorkflowRevision


def public_revisions() -> ColumnElement[bool]:
    return exists().where(
        WorkflowRevision.workflow_id == WorkflowDefinition.id,
        WorkflowRevision.visibility == "public",
    )


def visible_definitions(actor: CurrentUser) -> ColumnElement[bool]:
    return or_(
        WorkflowDefinition.owner_user_id == actor.user_id,
        exists().where(
            WorkflowGrant.workflow_id == WorkflowDefinition.id,
            WorkflowGrant.user_id == actor.user_id,
        ),
        (WorkflowDefinition.status == "active")
        & (WorkflowDefinition.visibility == "public")
        & public_revisions(),
    )


def has_permission(
    session: Session, *, actor: CurrentUser, workflow: WorkflowDefinition, permission: Permission
) -> bool:
    if workflow.owner_user_id == actor.user_id:
        return True
    grant = session.scalar(
        select(WorkflowGrant).where(
            WorkflowGrant.workflow_id == workflow.id, WorkflowGrant.user_id == actor.user_id
        )
    )
    if grant is None:
        return False
    return permission in grant.permissions or (permission == "read" and bool(grant.permissions))


def load_workflow(
    session: Session,
    *,
    actor: CurrentUser,
    workflow_id: UUID,
    permission: Permission = "read",
    owner: bool = False,
    lock: bool = False,
) -> WorkflowDefinition:
    actor = current_user(session, actor.user_id)
    query = select(WorkflowDefinition).where(WorkflowDefinition.id == workflow_id)
    if lock:
        query = query.with_for_update()
    workflow = session.scalar(query.execution_options(populate_existing=True))
    if workflow is None:
        raise NotFoundError("The Workflow does not exist")
    if owner:
        if workflow.owner_user_id != actor.user_id:
            raise ForbiddenError("Only the Workflow owner may manage publication and sharing")
    elif not has_permission(
        session, actor=actor, workflow=workflow, permission=permission
    ) and not (
        permission in {"read", "use"}
        and workflow.status == "active"
        and workflow.visibility == "public"
        and session.scalar(select(public_revisions()).where(WorkflowDefinition.id == workflow.id))
    ):
        raise NotFoundError("The Workflow does not exist")
    if permission in {"edit", "use"} and workflow.status != "active":
        raise ConflictError("The Workflow is archived")
    return workflow


def load_revision(
    session: Session,
    *,
    actor: CurrentUser,
    revision_id: UUID,
    use: bool = False,
) -> tuple[WorkflowRevision, WorkflowDefinition]:
    revision = session.get(WorkflowRevision, revision_id, populate_existing=True)
    if revision is None:
        raise NotFoundError("The Workflow revision does not exist")
    workflow = load_workflow(
        session, actor=actor, workflow_id=revision.workflow_id, permission="use" if use else "read"
    )
    if revision.visibility != "public" and not has_permission(
        session, actor=actor, workflow=workflow, permission="use" if use else "read"
    ):
        raise NotFoundError("The Workflow revision does not exist")
    return revision, workflow
