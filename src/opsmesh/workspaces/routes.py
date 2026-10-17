"""Workspace lifecycle APIs: create, query, modify, archive, transfer, delete.

Authentication resolves the current user; every operation authorizes
through the workspace service, which uses the one space access path. A
platform administrator gets no tenant permission from these routes.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, PageParams
from opsmesh.workspaces import service
from opsmesh.workspaces.models import Workspace
from opsmesh.workspaces.schemas import (
    CapacityDefaults,
    TransferOwnershipRequest,
    WorkspaceCreateRequest,
    WorkspacePage,
    WorkspaceUpdateRequest,
    WorkspaceView,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

WorkspaceIdPath = Annotated[UUID, Path()]
LimitQuery = Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)]
OffsetQuery = Annotated[int, Query(ge=0)]


def _capacity_defaults(request: Request) -> CapacityDefaults:
    """The configured initial capacity limits of a new workspace."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None:  # pragma: no cover - assembly guard
        return CapacityDefaults()
    return CapacityDefaults(
        concurrency_limit=settings.default_workspace_concurrency_limit,
        environment_limit=settings.default_workspace_environment_limit,
    )


CapacityDefaultsDep = Annotated[CapacityDefaults, Depends(_capacity_defaults)]


def _view(workspace: Workspace) -> WorkspaceView:
    return WorkspaceView(
        workspace_id=workspace.id,
        name=workspace.name,
        description=workspace.description,
        status=workspace.status,
        created_by_user_id=workspace.created_by_user_id,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )


@router.post(
    "",
    response_model=WorkspaceView,
    status_code=status.HTTP_201_CREATED,
    operation_id="workspaces.create",
)
def create_workspace(
    payload: WorkspaceCreateRequest,
    session: DbSession,
    user: CurrentUserDep,
    capacity_defaults: CapacityDefaultsDep,
) -> WorkspaceView:
    """Create one workspace; the creator becomes its owner."""
    with session_transaction(session):
        workspace = service.create_workspace(
            session,
            actor=user,
            name=payload.name,
            description=payload.description,
            capacity_limits=capacity_defaults.as_limits(),
        )
    return _view(workspace)


@router.get("", response_model=WorkspacePage, operation_id="workspaces.list")
def list_workspaces(
    session: DbSession,
    user: CurrentUserDep,
    limit: LimitQuery = DEFAULT_PAGE_LIMIT,
    offset: OffsetQuery = 0,
) -> WorkspacePage:
    """List the workspaces the current user is a member of."""
    workspaces, total = service.list_workspaces(
        session, actor=user, page=PageParams(limit=limit, offset=offset)
    )
    return WorkspacePage(workspaces=[_view(item) for item in workspaces], total=total)


@router.get("/{workspace_id}", response_model=WorkspaceView, operation_id="workspaces.read")
def read_workspace(
    session: DbSession, user: CurrentUserDep, workspace_id: WorkspaceIdPath
) -> WorkspaceView:
    return _view(service.get_workspace(session, actor=user, workspace_id=workspace_id))


@router.patch("/{workspace_id}", response_model=WorkspaceView, operation_id="workspaces.update")
def update_workspace(
    payload: WorkspaceUpdateRequest,
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
) -> WorkspaceView:
    """Change the workspace metadata; an archived workspace refuses writes."""
    with session_transaction(session):
        workspace = service.update_workspace(
            session,
            actor=user,
            workspace_id=workspace_id,
            name=payload.name,
            description=payload.description,
        )
    return _view(workspace)


@router.post(
    "/{workspace_id}/archive", response_model=WorkspaceView, operation_id="workspaces.archive"
)
def archive_workspace(
    session: DbSession, user: CurrentUserDep, workspace_id: WorkspaceIdPath
) -> WorkspaceView:
    """Archive one workspace; reads stay open, writes are refused."""
    with session_transaction(session):
        workspace = service.archive_workspace(session, actor=user, workspace_id=workspace_id)
    return _view(workspace)


@router.post(
    "/{workspace_id}/transfer-ownership",
    response_model=WorkspaceView,
    operation_id="workspaces.transfer_ownership",
)
def transfer_workspace_ownership(
    payload: TransferOwnershipRequest,
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
) -> WorkspaceView:
    """Move the owner role to another member; the previous owner becomes admin."""
    with session_transaction(session):
        workspace = service.transfer_ownership(
            session,
            actor=user,
            workspace_id=workspace_id,
            new_owner_user_id=payload.new_owner_user_id,
        )
    return _view(workspace)


@router.delete(
    "/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT, operation_id="workspaces.delete"
)
def delete_workspace(
    session: DbSession, user: CurrentUserDep, workspace_id: WorkspaceIdPath
) -> Response:
    """Delete one workspace; its history stays and every access is refused."""
    with session_transaction(session):
        service.delete_workspace(session, actor=user, workspace_id=workspace_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
