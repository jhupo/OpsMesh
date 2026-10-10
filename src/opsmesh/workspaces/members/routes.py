from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.pagination import PageParams
from opsmesh.workspaces.management.errors import (
    WorkspaceMemberConflictError,
    WorkspaceMemberNotFoundError,
    WorkspaceMemberPermissionError,
)
from opsmesh.workspaces.management.service import WorkspaceService
from opsmesh.workspaces.members.schemas import (
    WorkspaceMemberCreateRequest,
    WorkspaceMemberResponse,
    WorkspaceMemberUpdateRequest,
)
from opsmesh.workspaces.members.service import WorkspaceMemberService

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("/{workspace_id}/members", response_model=PageResponse[WorkspaceMemberResponse])
def list_workspace_members(
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
def create_workspace_member(
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
def update_workspace_member(
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
def disable_workspace_member(
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
