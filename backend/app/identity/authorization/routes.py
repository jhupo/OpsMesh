"""Workspace resource grants and effective permissions for all API clients."""

from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.identity.auth.dependencies import get_current_user, workspace_dependency
from backend.app.identity.authorization.context import AuthenticatedUser, WorkspaceContext
from backend.app.identity.authorization.errors import PermissionDeniedError
from backend.app.identity.authorization.permissions import WorkspaceAction, WorkspaceRole
from backend.app.identity.authorization.resources import (
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from backend.app.identity.authorization.service import AuthorizationService
from backend.app.shared.db.session import get_db_session
from backend.app.shared.errors import ForbiddenError

router = APIRouter(prefix="/workspaces/{workspace_id}/access", tags=["resource-access"])


class WorkspaceAccessResponse(BaseModel):
    workspace_id: UUID
    user_id: UUID
    role: WorkspaceRole
    workspace_status: str
    membership_status: str
    allowed_actions: list[WorkspaceAction] = Field(
        description="Workspace-level permissions; resource-specific authorization still applies."
    )


@router.get("/context", response_model=WorkspaceAccessResponse)
def workspace_access_context(
    workspace_id: UUID,
    response: Response,
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> WorkspaceAccessResponse:
    try:
        context = AuthorizationService(session).get_access_context(
            workspace_id=workspace_id, user=user
        )
    except PermissionDeniedError as exc:
        raise ForbiddenError(str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return WorkspaceAccessResponse(
        workspace_id=context.workspace.id,
        user_id=context.user.user_id,
        role=context.role,
        workspace_status=context.workspace.status,
        membership_status=context.membership.status,
        allowed_actions=list(context.allowed_actions),
    )


class GrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actions: frozenset[ResourceAction] = Field(max_length=len(ResourceAction))


class GrantResponse(BaseModel):
    user_id: UUID
    actions: list[ResourceAction]


class PermissionResponse(BaseModel):
    kind: ResourceKind
    resource_id: UUID
    actions: list[ResourceAction]


class OwnerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID


@router.put("/{kind}/{resource_id}/owner", status_code=204)
def assign_owner(
    kind: ResourceKind,
    resource_id: UUID,
    payload: OwnerRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> None:
    ResourceAuthorizationService(session, context.user).assign_owner(
        context.workspace.id,
        kind,
        resource_id,
        payload.user_id,
    )
    session.commit()


@router.get("/{kind}/{resource_id}/permissions", response_model=PermissionResponse)
def effective_permissions(
    kind: ResourceKind,
    resource_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PermissionResponse:
    service = ResourceAuthorizationService(session, context.user)
    actions = service.effective_actions(context.workspace.id, kind, resource_id)
    return PermissionResponse(kind=kind, resource_id=resource_id, actions=actions)


@router.get("/{kind}/{resource_id}/grants", response_model=list[GrantResponse])
def list_grants(
    kind: ResourceKind,
    resource_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> list[GrantResponse]:
    grouped = ResourceAuthorizationService(session, context.user).list_grants(
        context.workspace.id,
        kind,
        resource_id,
    )
    return [GrantResponse(user_id=user_id, actions=actions) for user_id, actions in grouped.items()]


@router.put("/{kind}/{resource_id}/grants/{user_id}", response_model=GrantResponse)
def replace_grants(
    kind: ResourceKind,
    resource_id: UUID,
    user_id: UUID,
    payload: GrantRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> GrantResponse:
    ResourceAuthorizationService(session, context.user).replace_grants(
        context.workspace.id,
        kind,
        resource_id,
        user_id,
        payload.actions,
    )
    session.commit()
    return GrantResponse(user_id=user_id, actions=sorted(payload.actions))
