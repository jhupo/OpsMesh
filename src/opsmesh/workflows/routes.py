"""Workflow definition authoring and sharing API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.http.responses import page_response
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.security.redaction import RedactionRules
from opsmesh.users.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.workflows import access, invitations, publication, service
from opsmesh.workflows.bindings import resolve_spec
from opsmesh.workflows.schemas import (
    DraftView,
    GrantRequest,
    GrantView,
    InvitationCreate,
    InvitationDecision,
    InvitationView,
    PublishRequest,
    RevisionRequest,
    RevisionView,
    WorkflowCreate,
    WorkflowUpdate,
    WorkflowView,
)
from opsmesh.workflows.validation import validate_spec

router = APIRouter(prefix="/workflows", tags=["workflows"])
Pagination = Annotated[PageParams, Depends(pagination_params)]


def get_redaction_rules(request: Request) -> RedactionRules:
    rules = getattr(request.app.state, "redaction_rules", None)
    if not isinstance(rules, RedactionRules):
        raise HTTPException(status_code=503, detail="Workflow authoring rules are not configured")
    return rules


Redaction = Annotated[RedactionRules, Depends(get_redaction_rules)]


@router.post("", status_code=201, operation_id="workflows.create")
def create_workflow(
    payload: WorkflowCreate,
    session: DbSession,
    user: CurrentUserDep,
    redaction: Redaction,
) -> WorkflowView:
    with session_transaction(session):
        result = WorkflowView.model_validate(
            service.create_workflow(session, actor=user, request=payload, redaction=redaction)
        )
    return result


@router.get("", operation_id="workflows.list")
def list_workflows(
    session: DbSession,
    user: CurrentUserDep,
    page: Pagination,
    owned: bool = False,
) -> PageResponse[WorkflowView]:
    items, total = service.list_workflows(session, actor=user, page=page, owned=owned)
    return page_response(items, total, page, WorkflowView)


# Static paths precede /{workflow_id}; invitation access is checked against the
# authenticated recipient/owner, never the UUID alone.
@router.get("/invitations", operation_id="workflows.invitations.list")
def list_invitations(
    session: DbSession,
    user: CurrentUserDep,
    page: Pagination,
) -> PageResponse[InvitationView]:
    items, total = invitations.list_invitations(session, actor=user, page=page)
    return page_response(items, total, page, InvitationView)


@router.post("/invitations/{invitation_id}/decision", operation_id="workflows.invitations.decide")
def decide_invitation(
    invitation_id: UUID,
    payload: InvitationDecision,
    session: DbSession,
    user: CurrentUserDep,
) -> InvitationView:
    with session_transaction(session):
        result = InvitationView.model_validate(
            invitations.decide_invitation(
                session,
                actor=user,
                invitation_id=invitation_id,
                accept=payload.decision == "accept",
            )
        )
    return result


@router.delete(
    "/invitations/{invitation_id}", status_code=204, operation_id="workflows.invitations.revoke"
)
def revoke_invitation(invitation_id: UUID, session: DbSession, user: CurrentUserDep) -> Response:
    with session_transaction(session):
        invitations.revoke_invitation(session, actor=user, invitation_id=invitation_id)
    return Response(status_code=204)


@router.get("/revisions/{revision_id}", operation_id="workflows.revisions.read")
def read_revision(revision_id: UUID, session: DbSession, user: CurrentUserDep) -> RevisionView:
    revision, _ = access.load_revision(session, actor=user, revision_id=revision_id)
    return RevisionView.model_validate(revision)


@router.get("/{workflow_id}", operation_id="workflows.read")
def read_workflow(workflow_id: UUID, session: DbSession, user: CurrentUserDep) -> WorkflowView:
    return WorkflowView.model_validate(
        access.load_workflow(session, actor=user, workflow_id=workflow_id)
    )


@router.get("/{workflow_id}/draft", operation_id="workflows.draft.read")
def read_draft(workflow_id: UUID, session: DbSession, user: CurrentUserDep) -> DraftView:
    revision, spec = service.read_draft(session, actor=user, workflow_id=workflow_id)
    return DraftView(revision=revision, spec=spec)


@router.put("/{workflow_id}/draft", operation_id="workflows.draft.update")
def update_workflow(
    workflow_id: UUID,
    payload: WorkflowUpdate,
    session: DbSession,
    user: CurrentUserDep,
    redaction: Redaction,
) -> WorkflowView:
    with session_transaction(session):
        result = WorkflowView.model_validate(
            service.update_workflow(
                session, actor=user, workflow_id=workflow_id, request=payload, redaction=redaction
            )
        )
    return result


@router.post("/{workflow_id}/validate", operation_id="workflows.validate")
def validate_workflow(
    workflow_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    redaction: Redaction,
) -> DraftView:
    revision, spec = service.read_draft(session, actor=user, workflow_id=workflow_id)
    validate_spec(spec, redaction=redaction)
    resolve_spec(session, actor=user, spec=spec, workflow_id=workflow_id)
    return DraftView(revision=revision, spec=spec)


@router.post("/{workflow_id}/publish", status_code=201, operation_id="workflows.publish")
def publish_workflow(
    workflow_id: UUID,
    payload: PublishRequest,
    session: DbSession,
    user: CurrentUserDep,
    redaction: Redaction,
) -> RevisionView:
    with session_transaction(session):
        result = RevisionView.model_validate(
            publication.publish_workflow(
                session,
                actor=user,
                workflow_id=workflow_id,
                expected_revision=payload.expected_revision,
                visibility=payload.visibility,
                redaction=redaction,
            )
        )
    return result


@router.post("/{workflow_id}/archive", status_code=204, operation_id="workflows.archive")
def archive_workflow(
    workflow_id: UUID,
    payload: RevisionRequest,
    session: DbSession,
    user: CurrentUserDep,
) -> Response:
    with session_transaction(session):
        service.archive_workflow(
            session,
            actor=user,
            workflow_id=workflow_id,
            expected_revision=payload.expected_revision,
        )
    return Response(status_code=204)


@router.get("/{workflow_id}/revisions", operation_id="workflows.revisions.list")
def list_revisions(
    workflow_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    page: Pagination,
) -> PageResponse[RevisionView]:
    items, total = service.list_revisions(session, actor=user, workflow_id=workflow_id, page=page)
    return page_response(items, total, page, RevisionView)


@router.get("/{workflow_id}/grants", operation_id="workflows.grants.list")
def list_grants(
    workflow_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    page: Pagination,
) -> PageResponse[GrantView]:
    items, total = invitations.list_grants(session, actor=user, workflow_id=workflow_id, page=page)
    return page_response(items, total, page, GrantView)


@router.put("/{workflow_id}/grants/{user_id}", operation_id="workflows.grants.set")
def set_grant(
    workflow_id: UUID,
    user_id: UUID,
    payload: GrantRequest,
    session: DbSession,
    user: CurrentUserDep,
) -> GrantView:
    with session_transaction(session):
        result = GrantView.model_validate(
            invitations.set_grant(
                session,
                actor=user,
                workflow_id=workflow_id,
                user_id=user_id,
                permissions=payload.permissions,
            )
        )
    return result


@router.delete(
    "/{workflow_id}/grants/{user_id}", status_code=204, operation_id="workflows.grants.revoke"
)
def revoke_grant(
    workflow_id: UUID,
    user_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
) -> Response:
    with session_transaction(session):
        invitations.revoke_grant(session, actor=user, workflow_id=workflow_id, user_id=user_id)
    return Response(status_code=204)


@router.post(
    "/{workflow_id}/invitations", status_code=201, operation_id="workflows.invitations.create"
)
def create_invitation(
    workflow_id: UUID,
    payload: InvitationCreate,
    session: DbSession,
    user: CurrentUserDep,
) -> InvitationView:
    with session_transaction(session):
        result = InvitationView.model_validate(
            invitations.create_invitation(
                session,
                actor=user,
                workflow_id=workflow_id,
                recipient_user_id=payload.recipient_user_id,
                permissions=payload.permissions,
                expires_in_hours=payload.expires_in_hours,
            )
        )
    return result
