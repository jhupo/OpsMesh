from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.api.schemas.workspaces import (
    WorkspaceMemberCreateRequest,
    WorkspaceMemberResponse,
    WorkspaceMemberUpdateRequest,
)
from backend.app.api.services.workspace_errors import (
    WorkspaceMemberConflictError,
    WorkspaceMemberNotFoundError,
    WorkspaceMemberPermissionError,
)
from backend.app.api.services.workspace_members import WorkspaceMemberService
from backend.app.api.services.workspaces import WorkspaceService
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.pagination import PageParams
from backend.app.db.session import get_db_session

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("/{workspace_id}/members", response_model=PageResponse[WorkspaceMemberResponse])
async def list_workspace_members(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_MEMBERS)),
    session: Session = Depends(get_db_session),
) -> PageResponse[WorkspaceMemberResponse]:
    items, total = WorkspaceService(session).list_members(context.workspace.id, page)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "/{workspace_id}/members",
    response_model=WorkspaceMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_member(
    request: WorkspaceMemberCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_MEMBERS)),
    session: Session = Depends(get_db_session),
) -> WorkspaceMemberResponse:
    try:
        member = WorkspaceMemberService(session).create_member(
            context.workspace.id,
            request,
            actor_user_id=context.user.user_id,
            actor_role=context.role.value,
        )
    except WorkspaceMemberNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except WorkspaceMemberPermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.message) from exc
    except WorkspaceMemberConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return WorkspaceMemberResponse.model_validate(member)


@router.patch(
    "/{workspace_id}/members/{member_id}",
    response_model=WorkspaceMemberResponse,
)
async def update_workspace_member(
    member_id: UUID,
    request: WorkspaceMemberUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_MEMBERS)),
    session: Session = Depends(get_db_session),
) -> WorkspaceMemberResponse:
    try:
        member = WorkspaceMemberService(session).update_member(
            context.workspace.id,
            member_id,
            request,
            actor_user_id=context.user.user_id,
            actor_role=context.role.value,
        )
    except WorkspaceMemberNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except WorkspaceMemberPermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.message) from exc
    except WorkspaceMemberConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return WorkspaceMemberResponse.model_validate(member)


@router.delete(
    "/{workspace_id}/members/{member_id}",
    response_model=WorkspaceMemberResponse,
)
async def disable_workspace_member(
    member_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_MEMBERS)),
    session: Session = Depends(get_db_session),
) -> WorkspaceMemberResponse:
    try:
        member = WorkspaceMemberService(session).disable_member(
            context.workspace.id,
            member_id,
            actor_user_id=context.user.user_id,
            actor_role=context.role.value,
        )
    except WorkspaceMemberNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except WorkspaceMemberPermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.message) from exc
    except WorkspaceMemberConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return WorkspaceMemberResponse.model_validate(member)
