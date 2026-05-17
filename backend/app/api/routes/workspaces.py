from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.workspaces import (
    WorkspaceCreateRequest,
    WorkspaceMemberResponse,
    WorkspaceResponse,
    WorkspaceUpdateRequest,
)
from backend.app.api.services.workspaces import WorkspaceService
from backend.app.auth.context import AuthenticatedUser, WorkspaceContext
from backend.app.auth.dependencies import get_current_user, workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.db.errors import DatabaseConflictError
from backend.app.db.session import get_db_session

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("", response_model=PageResponse[WorkspaceResponse])
async def list_workspaces(
    page: PageParams = Depends(pagination_params),
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> PageResponse[WorkspaceResponse]:
    items, total = WorkspaceService(session).list_for_user(current_user.user_id, page)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    request: WorkspaceCreateRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> WorkspaceResponse:
    try:
        workspace = WorkspaceService(session).create_for_owner(current_user.user_id, request)
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return WorkspaceResponse.model_validate(workspace)


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
) -> WorkspaceResponse:
    return WorkspaceResponse.model_validate(context.workspace)


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    request: WorkspaceUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> WorkspaceResponse:
    workspace = WorkspaceService(session).get_scoped(context.workspace.id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return WorkspaceResponse.model_validate(WorkspaceService(session).update(workspace, request))


@router.get("/{workspace_id}/members", response_model=PageResponse[WorkspaceMemberResponse])
async def list_workspace_members(
    workspace_id: UUID,
    page: PageParams = Depends(pagination_params),
    _: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> PageResponse[WorkspaceMemberResponse]:
    items, total = WorkspaceService(session).list_members(workspace_id, page)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)
