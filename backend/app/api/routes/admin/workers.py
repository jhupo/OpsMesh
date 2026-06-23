from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.admin.workers import AdminWorkerService
from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.routes.admin.dependencies import admin_worker_service
from backend.app.api.routes.admin.responses import page_response
from backend.app.api.schemas.admin import AdminWorkerNodeResponse, AdminWorkerUpdateRequest

router = APIRouter()


@router.get("/workers", response_model=PageResponse[AdminWorkerNodeResponse])
async def list_admin_workers(
    page: PageParams = Depends(pagination_params),
    status: str | None = Query(default=None),
    worker_type: str | None = Query(default=None),
    service: AdminWorkerService = Depends(admin_worker_service),
) -> PageResponse[AdminWorkerNodeResponse]:
    items, total = service.list_workers(page, status=status, worker_type=worker_type)
    return page_response(items, total, page, AdminWorkerNodeResponse)


@router.post("/workers/{worker_id}/drain", response_model=AdminWorkerNodeResponse)
async def drain_admin_worker(
    worker_id: str,
    service: AdminWorkerService = Depends(admin_worker_service),
) -> AdminWorkerNodeResponse:
    worker = service.drain_worker(worker_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found")
    return AdminWorkerNodeResponse.model_validate(worker)


@router.patch("/workers/{worker_id}", response_model=AdminWorkerNodeResponse)
async def update_admin_worker(
    worker_id: str,
    request: AdminWorkerUpdateRequest,
    service: AdminWorkerService = Depends(admin_worker_service),
) -> AdminWorkerNodeResponse:
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
    return AdminWorkerNodeResponse.model_validate(worker)
