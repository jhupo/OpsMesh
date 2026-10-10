from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from opsmesh.identity.authorization.admin_dependencies import require_platform_admin
from opsmesh.runtime.instances.admin_dependencies import admin_runtime_service
from opsmesh.runtime.instances.admin_schemas import (
    AdminForceStopRuntimeRequest,
    AdminWorkspaceRuntimeResponse,
)
from opsmesh.runtime.instances.admin_service import AdminRuntimeService
from opsmesh.runtime.queues.dependencies import get_worker_queue
from opsmesh.runtime.queues.service import RedisQueue
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.http.responses import page_response
from opsmesh.shared.pagination import PageParams

router = APIRouter(dependencies=[Depends(require_platform_admin)])


@router.get("/runtimes", response_model=PageResponse[AdminWorkspaceRuntimeResponse])
def list_admin_runtimes(
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
def force_stop_admin_runtime(
    runtime_id: UUID,
    request: AdminForceStopRuntimeRequest,
    service: AdminRuntimeService = Depends(admin_runtime_service),
    queue: RedisQueue = Depends(get_worker_queue),
) -> AdminWorkspaceRuntimeResponse:
    runtime = service.force_stop_runtime(runtime_id, reason=request.reason, queue=queue)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    return AdminWorkspaceRuntimeResponse.model_validate(runtime)
