from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.audit import AuditEventResponse
from backend.app.api.schemas.operations import (
    AuditEventFilterResponse,
    DeadLetterJobsResponse,
    FailedJobInspectionResponse,
    OperationsOverviewResponse,
    QueueMetricsResponse,
    RequeueDeadLetterResponse,
    RunEventFilterResponse,
    RuntimeCleanupResponse,
    RuntimeEventResponse,
    WorkerHeartbeatRequest,
    WorkerHeartbeatResponse,
)
from backend.app.api.schemas.runs import AgentRunResponse, RunEventResponse
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.operations.service import OperationsService
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

router = APIRouter(prefix="/workspaces/{workspace_id}/operations", tags=["operations"])


@router.post("/worker-heartbeats", response_model=WorkerHeartbeatResponse)
async def record_worker_heartbeat(
    request: WorkerHeartbeatRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> WorkerHeartbeatResponse:
    heartbeat = OperationsService(session).record_worker_heartbeat(
        workspace_id=request.workspace_id or context.workspace.id,
        worker_id=request.worker_id,
        worker_type=request.worker_type,
        status=request.status,
        queue_name=request.queue_name,
        details=request.details,
    )
    return WorkerHeartbeatResponse.model_validate(heartbeat)


@router.get("/queue-metrics", response_model=QueueMetricsResponse)
async def queue_metrics(
    queue_name: str = Query(default="agent_runs"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> QueueMetricsResponse:
    return OperationsService(
        session,
        redis,
        RedisKeyBuilder(settings.redis_key_prefix),
    ).queue_metrics(queue_name, context.workspace.id)


@router.get("/dead-letter-jobs", response_model=DeadLetterJobsResponse)
async def list_dead_letter_jobs(
    queue_name: str = Query(default="agent_runs"),
    limit: int = Query(default=50, ge=1, le=200),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> DeadLetterJobsResponse:
    return OperationsService(
        session,
        redis,
        RedisKeyBuilder(settings.redis_key_prefix),
    ).list_dead_letters(context.workspace.id, queue_name, limit)


@router.post("/dead-letter-jobs/{job_id}/requeue", response_model=RequeueDeadLetterResponse)
async def requeue_dead_letter_job(
    job_id: UUID,
    queue_name: str = Query(default="agent_runs"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> RequeueDeadLetterResponse:
    job = OperationsService(
        session,
        redis,
        RedisKeyBuilder(settings.redis_key_prefix),
    ).requeue_dead_letter(context.workspace.id, queue_name, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Dead-letter job not found")
    return RequeueDeadLetterResponse(requeued=True, job=job)


@router.get("/run-events", response_model=RunEventFilterResponse)
async def list_run_events(
    page: PageParams = Depends(pagination_params),
    event_type: str | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> RunEventFilterResponse:
    items, total = OperationsService(session).list_run_events(
        context.workspace.id,
        page,
        event_type,
    )
    return RunEventFilterResponse(
        items=[RunEventResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/runtime-events", response_model=PageResponse[RuntimeEventResponse])
async def list_runtime_events(
    page: PageParams = Depends(pagination_params),
    runtime_id: UUID | None = Query(default=None),
    event_type: str | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[RuntimeEventResponse]:
    items, total = OperationsService(session).list_runtime_events(
        context.workspace.id,
        page,
        runtime_id,
        event_type,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/runtime-cleanup", response_model=RuntimeCleanupResponse)
async def cleanup_runtimes(
    stale_after_seconds: int = Query(default=600, ge=60, le=86_400),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> RuntimeCleanupResponse:
    stale, deleted = OperationsService(session).cleanup_stale_runtimes(
        context.workspace.id,
        stale_after_seconds=stale_after_seconds,
    )
    return RuntimeCleanupResponse(stale_marked_offline=stale, deleted_records=deleted)


@router.get("/failed-runs", response_model=FailedJobInspectionResponse)
async def inspect_failed_runs(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> FailedJobInspectionResponse:
    runs, total = OperationsService(session).inspect_failed_runs(context.workspace.id, page)
    return FailedJobInspectionResponse(
        runs=[AgentRunResponse.model_validate(run) for run in runs],
        total=total,
    )


@router.get("/audit-events", response_model=AuditEventFilterResponse)
async def filter_audit_events(
    page: PageParams = Depends(pagination_params),
    action: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> AuditEventFilterResponse:
    items, total = OperationsService(session).filter_audit_events(
        context.workspace.id,
        page,
        action,
        target_type,
    )
    return AuditEventFilterResponse(
        items=[AuditEventResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/overview", response_model=OperationsOverviewResponse)
async def operations_overview(
    queue_name: str = Query(default="agent_runs"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> OperationsOverviewResponse:
    data = OperationsService(
        session,
        redis,
        RedisKeyBuilder(settings.redis_key_prefix),
    ).overview(context.workspace.id, queue_name)
    return OperationsOverviewResponse(**data)
