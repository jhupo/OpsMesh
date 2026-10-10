from uuid import UUID

from fastapi import APIRouter, Depends, Query

from opsmesh.identity.authorization.admin_dependencies import require_platform_admin
from opsmesh.runtime.instances.admin_dependencies import admin_runtime_service
from opsmesh.runtime.instances.admin_schemas import AdminRuntimeLeaseResponse
from opsmesh.runtime.instances.admin_service import AdminRuntimeService
from opsmesh.runtime.workers.admin_dependencies import admin_worker_service
from opsmesh.runtime.workers.admin_service import AdminWorkerService
from opsmesh.runtime.workers.schemas import WorkerLeaseResponse
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.http.responses import page_response
from opsmesh.shared.pagination import PageParams

router = APIRouter(dependencies=[Depends(require_platform_admin)])


@router.get("/worker-leases", response_model=PageResponse[WorkerLeaseResponse])
def list_admin_worker_leases(
    page: PageParams = Depends(pagination_params),
    status: str | None = Query(default=None),
    workspace_id: UUID | None = Query(default=None),
    worker_id: str | None = Query(default=None),
    service: AdminWorkerService = Depends(admin_worker_service),
) -> PageResponse[WorkerLeaseResponse]:
    items, total = service.list_worker_leases(
        page,
        status=status,
        workspace_id=workspace_id,
        worker_id=worker_id,
    )
    return page_response(items, total, page, WorkerLeaseResponse)


@router.get("/runtime-leases", response_model=PageResponse[AdminRuntimeLeaseResponse])
def list_admin_runtime_leases(
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
