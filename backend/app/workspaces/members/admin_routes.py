from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.identity.authorization.admin_dependencies import require_platform_admin
from backend.app.identity.users.models import User
from backend.app.shared.db.session import get_db_session
from backend.app.shared.http.pagination import PageResponse, pagination_params
from backend.app.shared.pagination import PageParams
from backend.app.workspaces.management.admin_service import AdminWorkspaceManagementService
from backend.app.workspaces.members.admin_schemas import (
    AdminWorkspaceMemberCreateRequest,
    AdminWorkspaceMemberResponse,
    AdminWorkspaceMemberUpdateRequest,
)
from backend.app.workspaces.members.admin_service import AdminWorkspaceMemberService
from backend.app.workspaces.members.models import WorkspaceMember

router = APIRouter(dependencies=[Depends(require_platform_admin)])


@router.get(
    "/workspaces/{workspace_id}/members",
    response_model=PageResponse[AdminWorkspaceMemberResponse],
)
def list_admin_workspace_members(
    workspace_id: UUID,
    page: PageParams = Depends(pagination_params),
    member_status: str | None = Query(default=None, alias="status"),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminWorkspaceMemberResponse]:
    service = AdminWorkspaceMemberService(session)
    if AdminWorkspaceManagementService(session).get_workspace(workspace_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    rows, total = service.list_members(workspace_id, page, status=member_status)
    items = [
        AdminWorkspaceMemberResponse(
            id=member.id,
            workspace_id=member.workspace_id,
            user_id=member.user_id,
            email=user.email,
            display_name=user.display_name,
            role=member.role,
            status=member.status,
            created_at=member.created_at,
            updated_at=member.updated_at,
        )
        for member, user in rows
    ]
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "/workspaces/{workspace_id}/members",
    response_model=AdminWorkspaceMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_admin_workspace_member(
    workspace_id: UUID,
    request: AdminWorkspaceMemberCreateRequest,
    session: Session = Depends(get_db_session),
) -> AdminWorkspaceMemberResponse:
    service = AdminWorkspaceMemberService(session)
    try:
        member = service.add_member(
            workspace_id,
            request.user_id,
            role=request.role,
            reason="Added by platform admin",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace or active user not found",
        )
    user = session.get(User, member.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return AdminWorkspaceMemberResponse(
        id=member.id,
        workspace_id=member.workspace_id,
        user_id=member.user_id,
        email=user.email,
        display_name=user.display_name,
        role=member.role,
        status=member.status,
        created_at=member.created_at,
        updated_at=member.updated_at,
    )


@router.patch(
    "/workspaces/{workspace_id}/members/{member_id}",
    response_model=AdminWorkspaceMemberResponse,
)
def update_admin_workspace_member(
    workspace_id: UUID,
    member_id: UUID,
    request: AdminWorkspaceMemberUpdateRequest,
    session: Session = Depends(get_db_session),
) -> AdminWorkspaceMemberResponse:
    service = AdminWorkspaceMemberService(session)
    try:
        member = service.update_member(
            workspace_id,
            member_id,
            role=request.role,
            status=request.status,
            reason="Updated by platform admin",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace member not found",
        )
    user = session.get(User, member.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return AdminWorkspaceMemberResponse(
        id=member.id,
        workspace_id=member.workspace_id,
        user_id=member.user_id,
        email=user.email,
        display_name=user.display_name,
        role=member.role,
        status=member.status,
        created_at=member.created_at,
        updated_at=member.updated_at,
    )


@router.delete(
    "/workspaces/{workspace_id}/members/{member_id}",
    response_model=AdminWorkspaceMemberResponse,
)
def remove_admin_workspace_member(
    workspace_id: UUID,
    member_id: UUID,
    session: Session = Depends(get_db_session),
) -> AdminWorkspaceMemberResponse:
    service = AdminWorkspaceMemberService(session)
    member = session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.id == member_id,
        )
    )
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace member not found",
        )
    try:
        member = service.remove_member(
            workspace_id,
            member.user_id,
            reason="Removed by platform admin",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace member not found",
        )
    user = session.get(User, member.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return AdminWorkspaceMemberResponse(
        id=member.id,
        workspace_id=member.workspace_id,
        user_id=member.user_id,
        email=user.email,
        display_name=user.display_name,
        role=member.role,
        status=member.status,
        created_at=member.created_at,
        updated_at=member.updated_at,
    )
