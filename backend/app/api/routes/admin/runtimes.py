from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.admin.runtime_control import AdminRuntimeService
from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.routes.admin.dependencies import admin_runtime_service
from backend.app.api.routes.admin.responses import page_response
from backend.app.api.schemas.admin import (
    AdminForceStopRuntimeRequest,
    AdminWorkspaceRuntimeResponse,
)
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue import RedisQueue

router = APIRouter()


@router.get("/runtimes", response_model=PageResponse[AdminWorkspaceRuntimeResponse])
async def list_admin_runtimes(
    page: PageParams = Depends(pagination_params),
    workspace_id: UUID | None = Query(default=None),
    runtime_space_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    connection_status: str | None = Query(default=None),
    service: AdminRuntimeService = Depends(admin_runtime_service),
) -> PageResponse[AdminWorkspaceRuntimeResponse]:
    items, total = service.list_runtimes(
        page,
        workspace_id=workspace_id,
        runtime_space_id=runtime_space_id,
        status=status,
        connection_status=connection_status,
    )
    return page_response(items, total, page, AdminWorkspaceRuntimeResponse)


@router.post("/runtimes/{runtime_id}/force-stop", response_model=AdminWorkspaceRuntimeResponse)
async def force_stop_admin_runtime(
    runtime_id: UUID,
    request: AdminForceStopRuntimeRequest,
    service: AdminRuntimeService = Depends(admin_runtime_service),
    queue: RedisQueue = Depends(get_worker_queue),
) -> AdminWorkspaceRuntimeResponse:
    runtime = service.force_stop_runtime(runtime_id, reason=request.reason, queue=queue)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    return AdminWorkspaceRuntimeResponse.model_validate(runtime)
