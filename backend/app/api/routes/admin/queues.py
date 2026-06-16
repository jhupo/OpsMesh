from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.admin.operations_summary import AdminOperationsSummaryService
from backend.app.admin.queue_operations import AdminQueueOperationsService
from backend.app.api.routes.admin.dependencies import (
    admin_operations_summary_service,
    admin_queue_operations_service,
)
from backend.app.api.schemas.admin import (
    AdminDeadLetterJobsResponse,
    AdminOperationsSummaryResponse,
    AdminQueueMetricsResponse,
    AdminRequeueDeadLetterResponse,
)

router = APIRouter()


@router.get("/queues/{queue_name}/metrics", response_model=AdminQueueMetricsResponse)
async def admin_queue_metrics(
    queue_name: str,
    service: AdminQueueOperationsService = Depends(admin_queue_operations_service),
) -> AdminQueueMetricsResponse:
    metrics = service.queue_metrics(queue_name)
    return AdminQueueMetricsResponse(**metrics.model_dump())


@router.get("/operations/summary", response_model=AdminOperationsSummaryResponse)
async def admin_operations_summary(
    queue_name: str = Query(default="agent_runs"),
    service: AdminOperationsSummaryService = Depends(admin_operations_summary_service),
) -> AdminOperationsSummaryResponse:
    return AdminOperationsSummaryResponse(**service.operations_summary(queue_name))


@router.get("/queues/{queue_name}/dead-letter-jobs", response_model=AdminDeadLetterJobsResponse)
async def list_admin_dead_letter_jobs(
    queue_name: str,
    limit: int = Query(default=50, ge=1, le=200),
    service: AdminQueueOperationsService = Depends(admin_queue_operations_service),
) -> AdminDeadLetterJobsResponse:
    items, total = service.list_dead_letters(queue_name, limit)
    return AdminDeadLetterJobsResponse(items=items, total=total)


@router.post(
    "/queues/{queue_name}/dead-letter-jobs/{job_id}/requeue",
    response_model=AdminRequeueDeadLetterResponse,
)
async def requeue_admin_dead_letter_job(
    queue_name: str,
    job_id: UUID,
    service: AdminQueueOperationsService = Depends(admin_queue_operations_service),
) -> AdminRequeueDeadLetterResponse:
    job = service.requeue_dead_letter(queue_name, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Dead-letter job not found")
    return AdminRequeueDeadLetterResponse(requeued=True, job=job)
