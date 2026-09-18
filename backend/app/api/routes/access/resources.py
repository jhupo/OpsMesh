"""Workspace resource grants and effective permissions for all API clients."""

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.api.dependencies.auth import workspace_dependency
from backend.app.core.db.session import get_db_session
from backend.app.domains.access.context import WorkspaceContext
from backend.app.domains.access.permissions import WorkspaceAction
from backend.app.domains.access.resources import (
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/access", tags=["resource-access"])


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
