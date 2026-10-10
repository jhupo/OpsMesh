from fastapi import APIRouter, Depends, HTTPException, Query

from opsmesh.identity.authorization.admin_dependencies import require_platform_admin
from opsmesh.runtime.workers.admin_dependencies import admin_worker_service
from opsmesh.runtime.workers.admin_schemas import AdminWorkerUpdateRequest
from opsmesh.runtime.workers.admin_service import AdminWorkerService
from opsmesh.runtime.workers.schemas import WorkerNodeResponse
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.http.responses import page_response
from opsmesh.shared.pagination import PageParams

router = APIRouter(dependencies=[Depends(require_platform_admin)])


@router.get("/workers", response_model=PageResponse[WorkerNodeResponse])
def list_admin_workers(
    page: PageParams = Depends(pagination_params),
    status: str | None = Query(default=None),
    worker_type: str | None = Query(default=None),
    service: AdminWorkerService = Depends(admin_worker_service),
) -> PageResponse[WorkerNodeResponse]:
    items, total = service.list_workers(page, status=status, worker_type=worker_type)
    return page_response(items, total, page, WorkerNodeResponse)


@router.post("/workers/{worker_id}/drain", response_model=WorkerNodeResponse)
def drain_admin_worker(
    worker_id: str,
    service: AdminWorkerService = Depends(admin_worker_service),
) -> WorkerNodeResponse:
    worker = service.drain_worker(worker_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found")
    return WorkerNodeResponse.model_validate(worker)


@router.patch("/workers/{worker_id}", response_model=WorkerNodeResponse)
def update_admin_worker(
    worker_id: str,
    request: AdminWorkerUpdateRequest,
    service: AdminWorkerService = Depends(admin_worker_service),
) -> WorkerNodeResponse:
    worker = service.update_worker(
        worker_id,
        status=request.status,
        worker_type=request.worker_type,
        queue_name=request.queue_name,
        worker_version=request.worker_version,
        hostname=request.hostname,
        capacity=request.capacity,
        details=request.details,
        reason=request.reason,
        updated_by=request.updated_by,
    )
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found")
    return WorkerNodeResponse.model_validate(worker)
