"""Authoring and release queries. All mutations join the caller's transaction."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.shared.db.pagination import page_scalars
from opsmesh.shared.errors import ConflictError, InvalidArgumentError
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.security.redaction import RedactionRules, redact_sensitive_payload
from opsmesh.users.access import CurrentUser
from opsmesh.users.service import current_user
from opsmesh.workflows import access
from opsmesh.workflows.contracts import WorkflowSpec
from opsmesh.workflows.models import WorkflowDefinition, WorkflowRevision
from opsmesh.workflows.schemas import WorkflowCreate, WorkflowUpdate
from opsmesh.workflows.validation import validate_name, validate_spec


def _validate_authoring(request: WorkflowCreate, *, redaction: RedactionRules) -> None:
    validate_spec(request.spec, redaction=redaction)
    if "\x00" in request.description:
        raise InvalidArgumentError("The Workflow description contains a null character")
    metadata: dict[str, object] = {"name": request.name, "description": request.description}
    if redact_sensitive_payload(metadata, rules=redaction) != metadata:
        raise InvalidArgumentError("Workflow metadata must not contain literal credentials")


def create_workflow(
    session: Session,
    *,
    actor: CurrentUser,
    request: WorkflowCreate,
    redaction: RedactionRules,
) -> WorkflowDefinition:
    actor = current_user(session, actor.user_id)
    _validate_authoring(request, redaction=redaction)
    workflow = WorkflowDefinition(
        owner_user_id=actor.user_id,
        name=validate_name(request.name),
        description=request.description,
        draft=request.spec.model_dump(mode="json"),
    )
    session.add(workflow)
    session.flush()
    return workflow


def list_workflows(
    session: Session,
    *,
    actor: CurrentUser,
    page: PageParams,
    owned: bool = False,
) -> tuple[list[WorkflowDefinition], int]:
    actor = current_user(session, actor.user_id)
    query = select(WorkflowDefinition).where(
        WorkflowDefinition.owner_user_id == actor.user_id
        if owned
        else access.visible_definitions(actor)
    )
    return page_scalars(
        session, query.order_by(WorkflowDefinition.created_at, WorkflowDefinition.id), page
    )


def read_draft(
    session: Session, *, actor: CurrentUser, workflow_id: UUID
) -> tuple[int, WorkflowSpec]:
    workflow = access.load_workflow(
        session, actor=actor, workflow_id=workflow_id, permission="edit"
    )
    return workflow.revision, WorkflowSpec.model_validate(workflow.draft)


def update_workflow(
    session: Session,
    *,
    actor: CurrentUser,
    workflow_id: UUID,
    request: WorkflowUpdate,
    redaction: RedactionRules,
) -> WorkflowDefinition:
    workflow = access.load_workflow(
        session, actor=actor, workflow_id=workflow_id, permission="edit", lock=True
    )
    if workflow.revision != request.expected_revision:
        raise ConflictError("The Workflow revision changed")
    _validate_authoring(request, redaction=redaction)
    workflow.name = validate_name(request.name)
    workflow.description = request.description
    workflow.draft = request.spec.model_dump(mode="json")
    workflow.revision += 1
    session.flush()
    return workflow


def archive_workflow(
    session: Session,
    *,
    actor: CurrentUser,
    workflow_id: UUID,
    expected_revision: int,
) -> None:
    workflow = access.load_workflow(
        session, actor=actor, workflow_id=workflow_id, owner=True, lock=True
    )
    if workflow.revision != expected_revision:
        raise ConflictError("The Workflow revision changed")
    if workflow.status != "archived":
        workflow.status = "archived"
        workflow.revision += 1
        session.flush()


def list_revisions(
    session: Session,
    *,
    actor: CurrentUser,
    workflow_id: UUID,
    page: PageParams,
) -> tuple[list[WorkflowRevision], int]:
    workflow = access.load_workflow(session, actor=actor, workflow_id=workflow_id)
    query = select(WorkflowRevision).where(WorkflowRevision.workflow_id == workflow.id)
    if not access.has_permission(session, actor=actor, workflow=workflow, permission="read"):
        query = query.where(WorkflowRevision.visibility == "public")
    return page_scalars(session, query.order_by(WorkflowRevision.number.desc()), page)
