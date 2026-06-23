from fastapi import APIRouter, Depends, Query

from backend.app.admin.overview import AdminOverviewService
from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.routes.admin.dependencies import admin_overview_service
from backend.app.api.routes.admin.responses import page_response
from backend.app.api.schemas.admin import AdminOverviewResponse, AdminWorkspaceResponse

router = APIRouter()


@router.get("/overview", response_model=AdminOverviewResponse)
async def admin_overview(
    service: AdminOverviewService = Depends(admin_overview_service),
) -> AdminOverviewResponse:
    return AdminOverviewResponse(**service.overview())


@router.get("/workspaces", response_model=PageResponse[AdminWorkspaceResponse])
async def list_admin_workspaces(
    page: PageParams = Depends(pagination_params),
    status: str | None = Query(default=None),
    service: AdminOverviewService = Depends(admin_overview_service),
) -> PageResponse[AdminWorkspaceResponse]:
    items, total = service.list_workspaces(page, status=status)
    return page_response(items, total, page, AdminWorkspaceResponse)
