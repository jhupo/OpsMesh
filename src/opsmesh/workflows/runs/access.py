"""Private workflow run access and current execution authority."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.shared.errors import ConflictError, NotFoundError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.users.access import CurrentUser
from opsmesh.users.service import load_identity
from opsmesh.workflows.access import current_actor
from opsmesh.workflows.bindings import authorize_bindings
from opsmesh.workflows.contracts import PreparedWorkflow
from opsmesh.workflows.instances.access import get_instance
from opsmesh.workflows.runs.models import WorkflowRun


def get_run(
    session: Session, *, actor: CurrentUser, run_id: UUID, lock: bool = False
) -> WorkflowRun:
    actor = current_actor(session, actor)
    query = select(WorkflowRun).where(
        WorkflowRun.id == run_id, WorkflowRun.user_id == actor.user_id
    )
    if lock:
        query = query.with_for_update()
    row = session.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise NotFoundError("The workflow run does not exist")
    return row


def authorize_run(
    session: Session, run_id: UUID, cipher: SecretCipher, *, lock: bool = False
) -> tuple[WorkflowRun, CurrentUser, PreparedWorkflow]:
    row = session.get(WorkflowRun, run_id, with_for_update=lock, populate_existing=True)
    if row is None:
        raise NotFoundError("The workflow run does not exist")
    actor = load_identity(session, row.user_id)
    if actor is None:
        raise NotFoundError("The workflow user does not exist")
    actor = current_actor(session, actor)
    if row.status != "running":
        raise ConflictError("The workflow run is not active")
    instance = get_instance(session, actor=actor, instance_id=row.instance_id)
    if instance.status != "active":
        raise ConflictError("The workflow instance is disabled")
    prepared = PreparedWorkflow.model_validate(cipher.decrypt_payload(instance.encrypted_prepared))
    authorize_bindings(session, actor=actor, prepared=prepared)
    return row, actor, prepared
