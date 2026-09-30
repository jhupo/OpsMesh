from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.api.routes.platform.responses import page_response
from backend.app.api.schemas.platform.admin import (
    AdminProjectQuotaResponse,
    AdminProjectQuotaUpsertRequest,
    AdminProjectResponse,
    AdminProjectStatusUpdateRequest,
    AdminSystemLogResponse,
)
from backend.app.core.db.session import get_db_session
from backend.app.core.pagination import PageParams
from backend.app.domains.platform.admin.management import (
    AdminResourceAuthorizationService,
    AdminSystemLogService,
    AdminWorkspaceManagementService,
)
from backend.app.identity.authorization.admin_schemas import (
    AdminResourceAuthorizationResponse,
    AdminResourceGrantResponse,
    AdminResourceGrantUpdateRequest,
    AdminResourceOwnerUpdateRequest,
)
from backend.app.identity.authorization.models import SecuredResource
from backend.app.identity.authorization.resources import ResourceAction, ResourceKind
from backend.app.identity.users.models import User
from backend.app.workspaces.management.admin_schemas import (
    AdminWorkspaceResponse,
    AdminWorkspaceStatusUpdateRequest,
)
from backend.app.workspaces.management.models import Workspace
from backend.app.workspaces.members.admin_schemas import (
    AdminWorkspaceMemberCreateRequest,
    AdminWorkspaceMemberResponse,
    AdminWorkspaceMemberUpdateRequest,
)
from backend.app.workspaces.members.models import WorkspaceMember
from backend.app.workspaces.quotas.service import WorkspaceQuotaService

router = APIRouter()


def _workspace_response(
    workspace: Workspace,
    *,
    member_count: int,
    project_count: int,
) -> AdminWorkspaceResponse:
    return AdminWorkspaceResponse(
        id=workspace.id,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
        owner_user_id=workspace.owner_user_id,
        name=workspace.name,
        slug=workspace.slug,
        status=workspace.status,
        settings=workspace.settings,
        member_count=member_count,
        project_count=project_count,
    )


@router.get(
    "/workspaces/{workspace_id}",
    response_model=AdminWorkspaceResponse,
)
async def get_admin_workspace(
    workspace_id: UUID,
    session: Session = Depends(get_db_session),
) -> AdminWorkspaceResponse:
    service = AdminWorkspaceManagementService(session)
    workspace = service.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    member_count, project_count = service.workspace_counts(workspace.id)
    return _workspace_response(
        workspace,
        member_count=member_count,
        project_count=project_count,
    )


@router.patch(
    "/workspaces/{workspace_id}/status",
    response_model=AdminWorkspaceResponse,
)
async def update_admin_workspace_status(
    workspace_id: UUID,
    request: AdminWorkspaceStatusUpdateRequest,
    session: Session = Depends(get_db_session),
) -> AdminWorkspaceResponse:
    service = AdminWorkspaceManagementService(session)
    workspace = service.update_workspace_status(
        workspace_id,
        status=request.status,
        reason=request.reason,
    )
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    member_count, project_count = service.workspace_counts(workspace.id)
    return _workspace_response(
        workspace,
        member_count=member_count,
        project_count=project_count,
    )


@router.get(
    "/workspaces/{workspace_id}/members",
    response_model=PageResponse[AdminWorkspaceMemberResponse],
)
async def list_admin_workspace_members(
    workspace_id: UUID,
    page: PageParams = Depends(pagination_params),
    member_status: str | None = Query(default=None, alias="status"),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminWorkspaceMemberResponse]:
    service = AdminWorkspaceManagementService(session)
    if service.get_workspace(workspace_id) is None:
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
async def add_admin_workspace_member(
    workspace_id: UUID,
    request: AdminWorkspaceMemberCreateRequest,
    session: Session = Depends(get_db_session),
) -> AdminWorkspaceMemberResponse:
    service = AdminWorkspaceManagementService(session)
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
async def update_admin_workspace_member(
    workspace_id: UUID,
    member_id: UUID,
    request: AdminWorkspaceMemberUpdateRequest,
    session: Session = Depends(get_db_session),
) -> AdminWorkspaceMemberResponse:
    service = AdminWorkspaceManagementService(session)
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
async def remove_admin_workspace_member(
    workspace_id: UUID,
    member_id: UUID,
    session: Session = Depends(get_db_session),
) -> AdminWorkspaceMemberResponse:
    service = AdminWorkspaceManagementService(session)
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


@router.get(
    "/workspaces/{workspace_id}/projects",
    response_model=PageResponse[AdminProjectResponse],
)
async def list_admin_workspace_projects(
    workspace_id: UUID,
    page: PageParams = Depends(pagination_params),
    project_status: str | None = Query(default=None, alias="status"),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminProjectResponse]:
    service = AdminWorkspaceManagementService(session)
    if service.get_workspace(workspace_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    items, total = service.list_projects(workspace_id, page, status=project_status)
    return page_response(items, total, page, AdminProjectResponse)


@router.get(
    "/workspaces/{workspace_id}/projects/{project_id}",
    response_model=AdminProjectResponse,
)
async def get_admin_project(
    workspace_id: UUID,
    project_id: UUID,
    session: Session = Depends(get_db_session),
) -> AdminProjectResponse:
    project = AdminWorkspaceManagementService(session).get_project(workspace_id, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return AdminProjectResponse.model_validate(project)


@router.patch(
    "/workspaces/{workspace_id}/projects/{project_id}/status",
    response_model=AdminProjectResponse,
)
async def update_admin_project_status(
    workspace_id: UUID,
    project_id: UUID,
    request: AdminProjectStatusUpdateRequest,
    session: Session = Depends(get_db_session),
) -> AdminProjectResponse:
    project = AdminWorkspaceManagementService(session).update_project_status(
        workspace_id,
        project_id,
        status=request.status,
        reason=request.reason,
    )
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return AdminProjectResponse.model_validate(project)


@router.get(
    "/workspaces/{workspace_id}/projects/{project_id}/quotas",
    response_model=list[AdminProjectQuotaResponse],
)
async def list_admin_project_quotas(
    workspace_id: UUID,
    project_id: UUID,
    session: Session = Depends(get_db_session),
) -> list[AdminProjectQuotaResponse]:
    management = AdminWorkspaceManagementService(session)
    if management.get_project(workspace_id, project_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    quotas = WorkspaceQuotaService(session).list_project_quotas(workspace_id, project_id)
    return [AdminProjectQuotaResponse.model_validate(quota) for quota in quotas]


@router.put(
    "/workspaces/{workspace_id}/projects/{project_id}/quotas",
    response_model=list[AdminProjectQuotaResponse],
)
async def upsert_admin_project_quotas(
    workspace_id: UUID,
    project_id: UUID,
    request: AdminProjectQuotaUpsertRequest,
    session: Session = Depends(get_db_session),
) -> list[AdminProjectQuotaResponse]:
    quotas = WorkspaceQuotaService(session).upsert_project_quotas(
        workspace_id,
        project_id,
        request,
    )
    if quotas is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return [AdminProjectQuotaResponse.model_validate(quota) for quota in quotas]


@router.delete(
    "/workspaces/{workspace_id}/projects/{project_id}/quotas/{quota_key}",
    response_model=AdminProjectQuotaResponse,
)
async def disable_admin_project_quota(
    workspace_id: UUID,
    project_id: UUID,
    quota_key: str,
    session: Session = Depends(get_db_session),
) -> AdminProjectQuotaResponse:
    quota = WorkspaceQuotaService(session).disable_project_quota(
        workspace_id,
        project_id,
        quota_key,
    )
    if quota is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project quota not found")
    return AdminProjectQuotaResponse.model_validate(quota)


def _admin_resource_authorization_response(
    workspace_id: UUID,
    kind: ResourceKind,
    resource_id: UUID,
    resource: SecuredResource,
    grants: list[tuple[UUID, list[ResourceAction]]],
) -> AdminResourceAuthorizationResponse:
    return AdminResourceAuthorizationResponse(
        workspace_id=workspace_id,
        resource_kind=kind,
        resource_id=resource_id,
        owner_user_id=resource.owner_user_id,
        grants=[
            AdminResourceGrantResponse(user_id=user_id, actions=actions)
            for user_id, actions in grants
        ],
    )


@router.get(
    "/workspaces/{workspace_id}/resources/{kind}/{resource_id}/authorization",
    response_model=AdminResourceAuthorizationResponse,
)
async def get_admin_resource_authorization(
    workspace_id: UUID,
    kind: ResourceKind,
    resource_id: UUID,
    session: Session = Depends(get_db_session),
) -> AdminResourceAuthorizationResponse:
    authorization = AdminResourceAuthorizationService(session).get_authorization(
        workspace_id,
        kind,
        resource_id,
    )
    if authorization is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    resource, grants = authorization
    return _admin_resource_authorization_response(
        workspace_id, kind, resource_id, resource, grants
    )


@router.put(
    "/workspaces/{workspace_id}/resources/{kind}/{resource_id}/owner",
    response_model=AdminResourceAuthorizationResponse,
)
async def assign_admin_resource_owner(
    workspace_id: UUID,
    kind: ResourceKind,
    resource_id: UUID,
    request: AdminResourceOwnerUpdateRequest,
    session: Session = Depends(get_db_session),
) -> AdminResourceAuthorizationResponse:
    service = AdminResourceAuthorizationService(session)
    try:
        resource = service.assign_owner(workspace_id, kind, resource_id, request.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if resource is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    authorization = service.get_authorization(workspace_id, kind, resource_id)
    assert authorization is not None
    current, grants = authorization
    return _admin_resource_authorization_response(
        workspace_id, kind, resource_id, current, grants
    )


@router.put(
    "/workspaces/{workspace_id}/resources/{kind}/{resource_id}/grants",
    response_model=AdminResourceAuthorizationResponse,
)
async def replace_admin_resource_grants(
    workspace_id: UUID,
    kind: ResourceKind,
    resource_id: UUID,
    request: AdminResourceGrantUpdateRequest,
    session: Session = Depends(get_db_session),
) -> AdminResourceAuthorizationResponse:
    service = AdminResourceAuthorizationService(session)
    try:
        updated = service.replace_grants(
            workspace_id,
            kind,
            resource_id,
            request.user_id,
            request.actions,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    authorization = service.get_authorization(workspace_id, kind, resource_id)
    assert authorization is not None
    resource, grants = authorization
    return _admin_resource_authorization_response(
        workspace_id, kind, resource_id, resource, grants
    )


@router.get(
    "/system/logs",
    response_model=PageResponse[AdminSystemLogResponse],
)
async def list_admin_system_logs(
    page: PageParams = Depends(pagination_params),
    workspace_id: UUID | None = Query(default=None),
    user_id: UUID | None = Query(default=None),
    action: str | None = Query(default=None, min_length=1, max_length=120),
    actor_type: str | None = Query(default=None, min_length=1, max_length=32),
    target_type: str | None = Query(default=None, min_length=1, max_length=120),
    created_after: datetime | None = Query(default=None),
    created_before: datetime | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminSystemLogResponse]:
    items, total = AdminSystemLogService(session).list_audit_events(
        page,
        workspace_id=workspace_id,
        user_id=user_id,
        action=action,
        actor_type=actor_type,
        target_type=target_type,
        created_after=created_after,
        created_before=created_before,
    )
    return page_response(items, total, page, AdminSystemLogResponse)
