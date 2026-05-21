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
    OperationsCapacityResponse,
    OperationsControlPlaneResponse,
    OperationsMcpJobsResponse,
    OperationsOutcomesResponse,
    OperationsOverviewResponse,
    OperationsRuntimeCapacityResponse,
    OperationsSchedulerResponse,
    OperationsSelfHostedMachinesResponse,
    QueueMetricsResponse,
    RequeueDeadLetterResponse,
    RunEventFilterResponse,
    RuntimeCleanupResponse,
    RuntimeEventResponse,
    RuntimeLeaseResponse,
    SecurityEventFilterResponse,
    SecurityEventResponse,
    WorkerHeartbeatRequest,
    WorkerHeartbeatResponse,
    WorkerLeaseResponse,
    WorkerNodeResponse,
)
from backend.app.api.schemas.runs import AgentRunResponse, RunEventResponse
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.operations.service import OperationsService
from backend.app.redis.cache import RedisJsonCache
from backend.app.redis.dependencies import get_cache_service, get_redis_client
from backend.app.redis.keys import RedisKeyBuilder

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

router = APIRouter(prefix="/workspaces/{workspace_id}/operations", tags=["operations"])


@router.post("/worker-heartbeats", response_model=WorkerHeartbeatResponse)
async def record_worker_heartbeat(
    request: WorkerHeartbeatRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
    session: Session = Depends(get_db_session),
) -> WorkerHeartbeatResponse:
    heartbeat = OperationsService(session).record_worker_heartbeat(
        workspace_id=request.workspace_id or context.workspace.id,
        worker_id=request.worker_id,
        worker_type=request.worker_type,
        status=request.status,
        queue_name=request.queue_name,
        details=request.details,
        worker_version=request.worker_version,
        hostname=request.hostname,
        capacity=request.capacity,
    )
    return WorkerHeartbeatResponse.model_validate(heartbeat)


@router.get("/workers", response_model=PageResponse[WorkerNodeResponse])
async def list_workers(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    worker_type: str | None = Query(default=None),
    _: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> PageResponse[WorkerNodeResponse]:
    items, total = OperationsService(session).list_worker_nodes(
        page,
        status=status_filter,
        worker_type=worker_type,
    )
    return PageResponse(
        items=[WorkerNodeResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("/workers/{worker_id}/drain", response_model=WorkerNodeResponse)
async def drain_worker(
    worker_id: str,
    _: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> WorkerNodeResponse:
    node = OperationsService(session).request_worker_drain(worker_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Worker not found")
    return WorkerNodeResponse.model_validate(node)


@router.get("/worker-leases", response_model=PageResponse[WorkerLeaseResponse])
async def list_worker_leases(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    worker_id: str | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
    session: Session = Depends(get_db_session),
) -> PageResponse[WorkerLeaseResponse]:
    items, total = OperationsService(session).list_worker_leases(
        context.workspace.id,
        page,
        status=status_filter,
        worker_id=worker_id,
    )
    return PageResponse(
        items=[WorkerLeaseResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/runtime-leases", response_model=PageResponse[RuntimeLeaseResponse])
async def list_runtime_leases(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    runtime_space_id: UUID | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
    session: Session = Depends(get_db_session),
) -> PageResponse[RuntimeLeaseResponse]:
    items, total = OperationsService(session).list_runtime_leases(
        context.workspace.id,
        page,
        status=status_filter,
        runtime_space_id=runtime_space_id,
    )
    return PageResponse(
        items=[RuntimeLeaseResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/queue-metrics", response_model=QueueMetricsResponse)
async def queue_metrics(
    queue_name: str = Query(default="agent_runs"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
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
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
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
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
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
    stale_lease_after_seconds: int = Query(default=900, ge=60, le=86_400),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
    session: Session = Depends(get_db_session),
) -> RuntimeCleanupResponse:
    service = OperationsService(session)
    stale, deleted = service.cleanup_stale_runtimes(
        context.workspace.id,
        stale_after_seconds=stale_after_seconds,
    )
    expired_leases = service.expire_stale_worker_leases(
        workspace_id=context.workspace.id,
        stale_after_seconds=stale_lease_after_seconds,
    )
    return RuntimeCleanupResponse(
        stale_marked_offline=stale,
        deleted_records=deleted,
        expired_worker_leases=expired_leases,
    )


@router.get("/failed-runs", response_model=FailedJobInspectionResponse)
async def inspect_failed_runs(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
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
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
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


@router.get("/security-events", response_model=SecurityEventFilterResponse)
async def filter_security_events(
    page: PageParams = Depends(pagination_params),
    action: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    user_id: UUID | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
    session: Session = Depends(get_db_session),
) -> SecurityEventFilterResponse:
    items, total = OperationsService(session).filter_security_events(
        context.workspace.id,
        page,
        action,
        severity,
        user_id,
    )
    return SecurityEventFilterResponse(
        items=[SecurityEventResponse.model_validate(item) for item in items],
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
    cache: RedisJsonCache = Depends(get_cache_service),
    settings: Settings = Depends(get_settings),
) -> OperationsOverviewResponse:
    cache_key = f"overview:{context.workspace.id}:{queue_name}"
    cached = cache.get_or_set(
        cache_key,
        lambda: OperationsService(
            session,
            redis,
            RedisKeyBuilder(settings.redis_key_prefix),
        ).overview_payload(context.workspace.id, queue_name),
        ttl_seconds=10,
    )
    return OperationsOverviewResponse(**cached.value)


@router.get("/control-plane", response_model=OperationsControlPlaneResponse)
async def operations_control_plane(
    queue_name: str = Query(default="agent_runs"),
    window_seconds: int = Query(default=86_400, ge=60, le=2_592_000),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    cache: RedisJsonCache = Depends(get_cache_service),
    settings: Settings = Depends(get_settings),
) -> OperationsControlPlaneResponse:
    cache_key = f"control-plane:{context.workspace.id}:{queue_name}:{window_seconds}"
    cached = cache.get_or_set(
        cache_key,
        lambda: OperationsService(
            session,
            redis,
            RedisKeyBuilder(settings.redis_key_prefix),
        )
        .control_plane_payload(
            context.workspace.id,
            queue_name,
            window_seconds=window_seconds,
        )
        .model_dump(mode="json"),
        ttl_seconds=10,
    )
    return OperationsControlPlaneResponse(**cached.value)


@router.get("/capacity", response_model=OperationsCapacityResponse)
async def operations_capacity(
    queue_name: str = Query(default="agent_runs"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    cache: RedisJsonCache = Depends(get_cache_service),
    settings: Settings = Depends(get_settings),
) -> OperationsCapacityResponse:
    cache_key = f"capacity:{context.workspace.id}:{queue_name}"
    cached = cache.get_or_set(
        cache_key,
        lambda: OperationsService(
            session,
            redis,
            RedisKeyBuilder(settings.redis_key_prefix),
        )
        .capacity_payload(context.workspace.id, queue_name)
        .model_dump(mode="json"),
        ttl_seconds=10,
    )
    return OperationsCapacityResponse(**cached.value)


@router.get("/runtime-capacity", response_model=OperationsRuntimeCapacityResponse)
async def operations_runtime_capacity(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    cache: RedisJsonCache = Depends(get_cache_service),
) -> OperationsRuntimeCapacityResponse:
    cache_key = f"runtime-capacity:{context.workspace.id}"
    cached = cache.get_or_set(
        cache_key,
        lambda: OperationsService(session)
        .runtime_capacity_payload(context.workspace.id)
        .model_dump(mode="json"),
        ttl_seconds=10,
    )
    return OperationsRuntimeCapacityResponse(**cached.value)


@router.get("/mcp-jobs", response_model=OperationsMcpJobsResponse)
async def operations_mcp_jobs(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    cache: RedisJsonCache = Depends(get_cache_service),
) -> OperationsMcpJobsResponse:
    cache_key = f"mcp-jobs:{context.workspace.id}"
    cached = cache.get_or_set(
        cache_key,
        lambda: OperationsService(session)
        .mcp_jobs_payload(context.workspace.id)
        .model_dump(mode="json"),
        ttl_seconds=10,
    )
    return OperationsMcpJobsResponse(**cached.value)


@router.get("/self-hosted-machines", response_model=OperationsSelfHostedMachinesResponse)
async def operations_self_hosted_machines(
    stale_after_seconds: int = Query(default=600, ge=60, le=86_400),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    cache: RedisJsonCache = Depends(get_cache_service),
) -> OperationsSelfHostedMachinesResponse:
    cache_key = f"self-hosted-machines:{context.workspace.id}:{stale_after_seconds}"
    cached = cache.get_or_set(
        cache_key,
        lambda: OperationsService(session)
        .self_hosted_machines_payload(
            context.workspace.id,
            stale_after_seconds=stale_after_seconds,
        )
        .model_dump(mode="json"),
        ttl_seconds=10,
    )
    return OperationsSelfHostedMachinesResponse(**cached.value)


@router.get("/scheduler", response_model=OperationsSchedulerResponse)
async def operations_scheduler(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    cache: RedisJsonCache = Depends(get_cache_service),
) -> OperationsSchedulerResponse:
    cache_key = f"scheduler:{context.workspace.id}"
    cached = cache.get_or_set(
        cache_key,
        lambda: OperationsService(session)
        .scheduler_payload(context.workspace.id)
        .model_dump(mode="json"),
        ttl_seconds=10,
    )
    return OperationsSchedulerResponse(**cached.value)


@router.get("/outcomes", response_model=OperationsOutcomesResponse)
async def operations_outcomes(
    window_seconds: int = Query(default=86_400, ge=60, le=2_592_000),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    cache: RedisJsonCache = Depends(get_cache_service),
) -> OperationsOutcomesResponse:
    cache_key = f"outcomes:{context.workspace.id}:{window_seconds}"
    cached = cache.get_or_set(
        cache_key,
        lambda: OperationsService(session)
        .outcomes_payload(
            context.workspace.id,
            window_seconds=window_seconds,
        )
        .model_dump(mode="json"),
        ttl_seconds=10,
    )
    return OperationsOutcomesResponse(**cached.value)
