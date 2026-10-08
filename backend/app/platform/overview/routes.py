from fastapi import APIRouter, Depends, Query

from backend.app.identity.authorization.admin_dependencies import require_platform_admin
from backend.app.platform.overview.dependencies import admin_overview_service
from backend.app.platform.overview.schemas import AdminOverviewResponse
from backend.app.platform.overview.service import AdminOverviewService
from backend.app.shared.http.pagination import PageResponse, pagination_params
from backend.app.shared.pagination import PageParams
from backend.app.workspaces.management.admin_dependencies import admin_workspace_management_service
from backend.app.workspaces.management.admin_schemas import AdminWorkspaceResponse
from backend.app.workspaces.management.admin_service import AdminWorkspaceManagementService

router = APIRouter(dependencies=[Depends(require_platform_admin)])


@router.get("/overview", response_model=AdminOverviewResponse)
async def admin_overview(
    service: AdminOverviewService = Depends(admin_overview_service),
) -> AdminOverviewResponse:
    return AdminOverviewResponse(**service.overview())


@router.get("/workspaces", response_model=PageResponse[AdminWorkspaceResponse])
async def list_admin_workspaces(
    page: PageParams = Depends(pagination_params),
    status: str | None = Query(default=None),
    service: AdminWorkspaceManagementService = Depends(admin_workspace_management_service),
) -> PageResponse[AdminWorkspaceResponse]:
    items, total = service.list_workspaces(page, status=status)
    response_items = []
    for workspace in items:
        member_count, project_count = service.workspace_counts(workspace.id)
        response_items.append(
            AdminWorkspaceResponse(
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
        )
    return PageResponse(items=response_items, total=total, limit=page.limit, offset=page.offset)
