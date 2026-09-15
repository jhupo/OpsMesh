from fastapi import APIRouter, Depends, Query

from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.api.routes.platform.dependencies import admin_overview_service
from backend.app.api.routes.platform.responses import page_response
from backend.app.api.schemas.platform.admin import AdminOverviewResponse
from backend.app.api.schemas.workspace.workspaces import WorkspaceResponse
from backend.app.core.pagination import PageParams
from backend.app.domains.platform.admin.overview import AdminOverviewService

router = APIRouter()


@router.get("/overview", response_model=AdminOverviewResponse)
async def admin_overview(
    service: AdminOverviewService = Depends(admin_overview_service),
) -> AdminOverviewResponse:
    return AdminOverviewResponse(**service.overview())


@router.get("/workspaces", response_model=PageResponse[WorkspaceResponse])
async def list_admin_workspaces(
    page: PageParams = Depends(pagination_params),
    status: str | None = Query(default=None),
    service: AdminOverviewService = Depends(admin_overview_service),
) -> PageResponse[WorkspaceResponse]:
    items, total = service.list_workspaces(page, status=status)
    return page_response(items, total, page, WorkspaceResponse)
