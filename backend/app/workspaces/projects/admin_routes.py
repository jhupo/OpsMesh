from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.identity.authorization.admin_dependencies import require_platform_admin
from backend.app.shared.db.session import get_db_session
from backend.app.shared.http.pagination import PageResponse, pagination_params
from backend.app.shared.http.responses import page_response
from backend.app.shared.pagination import PageParams
from backend.app.workspaces.management.admin_service import AdminWorkspaceManagementService
from backend.app.workspaces.projects.admin_schemas import (
    AdminProjectQuotaResponse,
    AdminProjectQuotaUpsertRequest,
    AdminProjectResponse,
    AdminProjectStatusUpdateRequest,
)
from backend.app.workspaces.projects.admin_service import AdminWorkspaceProjectService
from backend.app.workspaces.quotas.service import WorkspaceQuotaService

router = APIRouter(dependencies=[Depends(require_platform_admin)])





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
    service = AdminWorkspaceProjectService(session)
    if AdminWorkspaceManagementService(session).get_workspace(workspace_id) is None:
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
    project = AdminWorkspaceProjectService(session).get_project(workspace_id, project_id)
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
    project = AdminWorkspaceProjectService(session).update_project_status(
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
    management = AdminWorkspaceProjectService(session)
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
