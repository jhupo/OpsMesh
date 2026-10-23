"""Freeze a validated graph; publishing never imports code or starts a model/MCP."""

from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.shared.errors import ConflictError
from opsmesh.shared.security.redaction import RedactionRules
from opsmesh.shared.serialization import payload_digest
from opsmesh.users.access import CurrentUser
from opsmesh.workflows import access
from opsmesh.workflows.bindings import resolve_spec
from opsmesh.workflows.contracts import Visibility, WorkflowSpec
from opsmesh.workflows.models import WorkflowRevision
from opsmesh.workflows.validation import validate_spec


def publish_workflow(
    session: Session,
    *,
    actor: CurrentUser,
    workflow_id: UUID,
    expected_revision: int,
    visibility: Visibility,
    redaction: RedactionRules,
) -> WorkflowRevision:
    workflow = access.load_workflow(
        session, actor=actor, workflow_id=workflow_id, owner=True, permission="edit", lock=True
    )
    if workflow.revision != expected_revision:
        raise ConflictError("The Workflow revision changed")
    spec = WorkflowSpec.model_validate(workflow.draft)
    validate_spec(spec, redaction=redaction)
    resolve_spec(
        session, actor=actor, spec=spec, workflow_id=workflow.id, public=visibility == "public"
    )
    payload = spec.model_dump(mode="json")
    workflow.published_number += 1
    workflow.revision += 1
    workflow.visibility = visibility
    revision = WorkflowRevision(
        workflow_id=workflow.id,
        number=workflow.published_number,
        visibility=visibility,
        digest=payload_digest(payload),
        spec=payload,
        published_by_user_id=actor.user_id,
    )
    session.add(revision)
    session.flush()
    return revision
