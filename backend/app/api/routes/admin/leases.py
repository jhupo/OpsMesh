from uuid import UUID

from fastapi import APIRouter, Depends, Query

from backend.app.admin.runtime_control import AdminRuntimeService
from backend.app.admin.workers import AdminWorkerService
from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.api.routes.admin.dependencies import admin_runtime_service, admin_worker_service
from backend.app.api.routes.admin.responses import page_response
from backend.app.api.schemas.admin import AdminRuntimeLeaseResponse, AdminWorkerLeaseResponse
from backend.app.core.pagination import PageParams

router = APIRouter()


@router.get("/worker-leases", response_model=PageResponse[AdminWorkerLeaseResponse])
async def list_admin_worker_leases(
    page: PageParams = Depends(pagination_params),
    status: str | None = Query(default=None),
    workspace_id: UUID | None = Query(default=None),
    worker_id: str | None = Query(default=None),
    service: AdminWorkerService = Depends(admin_worker_service),
) -> PageResponse[AdminWorkerLeaseResponse]:
    items, total = service.list_worker_leases(
        page,
        status=status,
        workspace_id=workspace_id,
        worker_id=worker_id,
    )
    return page_response(items, total, page, AdminWorkerLeaseResponse)


@router.get("/runtime-leases", response_model=PageResponse[AdminRuntimeLeaseResponse])
async def list_admin_runtime_leases(
    page: PageParams = Depends(pagination_params),
    status: str | None = Query(default=None),
    workspace_id: UUID | None = Query(default=None),
    runtime_space_id: UUID | None = Query(default=None),
    workspace_runtime_id: UUID | None = Query(default=None),
    service: AdminRuntimeService = Depends(admin_runtime_service),
) -> PageResponse[AdminRuntimeLeaseResponse]:
    items, total = service.list_runtime_leases(
        page,
        status=status,
        workspace_id=workspace_id,
        runtime_space_id=runtime_space_id,
        workspace_runtime_id=workspace_runtime_id,
    )
    return page_response(items, total, page, AdminRuntimeLeaseResponse)
