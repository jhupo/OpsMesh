from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.admin.service import AdminControlPlaneService
from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.admin import (
    AdminDeadLetterJobsResponse,
    AdminForceStopRuntimeRequest,
    AdminOperationsSummaryResponse,
    AdminOverviewResponse,
    AdminPlatformPolicyResponse,
    AdminQuarantineRuntimeSpaceRequest,
    AdminQuarantineRuntimeSpaceResponse,
    AdminQueueMetricsResponse,
    AdminRequeueDeadLetterResponse,
    AdminRiskyExecutionPolicyUpdateRequest,
    AdminRuntimeSpaceResponse,
    AdminSecurityEventResponse,
    AdminSystemConfigurationResponse,
    AdminWorkerLeaseResponse,
    AdminWorkerNodeResponse,
    AdminWorkerUpdateRequest,
    AdminWorkspaceResponse,
    AdminWorkspaceRuntimeResponse,
)
from backend.app.auth.admin import require_platform_admin
from backend.app.core.config import Settings, get_settings
from backend.app.core.executors import blocking_executor_snapshot
from backend.app.core.resources import recommend_runtime_resources
from backend.app.db.session import database_pool_snapshot, get_db_session
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder

if TYPE_CHECKING:
    from redis import Redis

    RedisClient = Redis[str]
else:
    RedisClient = object

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_platform_admin)],
)


@router.get("/overview", response_model=AdminOverviewResponse)
async def admin_overview(session: Session = Depends(get_db_session)) -> AdminOverviewResponse:
    return AdminOverviewResponse(**AdminControlPlaneService(session).overview())


@router.get("/workspaces", response_model=PageResponse[AdminWorkspaceResponse])
async def list_admin_workspaces(
    page: PageParams = Depends(pagination_params),
    status: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminWorkspaceResponse]:
    items, total = AdminControlPlaneService(session).list_workspaces(page, status=status)
    return PageResponse(
        items=[AdminWorkspaceResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/workers", response_model=PageResponse[AdminWorkerNodeResponse])
async def list_admin_workers(
    page: PageParams = Depends(pagination_params),
    status: str | None = Query(default=None),
    worker_type: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminWorkerNodeResponse]:
    items, total = AdminControlPlaneService(session).list_workers(
        page,
        status=status,
        worker_type=worker_type,
    )
    return PageResponse(
        items=[AdminWorkerNodeResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("/workers/{worker_id}/drain", response_model=AdminWorkerNodeResponse)
async def drain_admin_worker(
    worker_id: str,
    session: Session = Depends(get_db_session),
) -> AdminWorkerNodeResponse:
    worker = AdminControlPlaneService(session).drain_worker(worker_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found")
    return AdminWorkerNodeResponse.model_validate(worker)


@router.patch("/workers/{worker_id}", response_model=AdminWorkerNodeResponse)
async def update_admin_worker(
    worker_id: str,
    request: AdminWorkerUpdateRequest,
    session: Session = Depends(get_db_session),
) -> AdminWorkerNodeResponse:
    worker = AdminControlPlaneService(session).update_worker(
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


@router.get("/runtime-spaces", response_model=PageResponse[AdminRuntimeSpaceResponse])
async def list_admin_runtime_spaces(
    page: PageParams = Depends(pagination_params),
    status: str | None = Query(default=None),
    workspace_id: UUID | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminRuntimeSpaceResponse]:
    items, total = AdminControlPlaneService(session).list_runtime_spaces(
        page,
        status=status,
        workspace_id=workspace_id,
    )
    return PageResponse(
        items=[AdminRuntimeSpaceResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/runtime-spaces/{runtime_space_id}/quarantine",
    response_model=AdminQuarantineRuntimeSpaceResponse,
)
async def quarantine_admin_runtime_space(
    runtime_space_id: UUID,
    request: AdminQuarantineRuntimeSpaceRequest,
    session: Session = Depends(get_db_session),
) -> AdminQuarantineRuntimeSpaceResponse:
    runtime_space = AdminControlPlaneService(session).quarantine_runtime_space(
        runtime_space_id,
        reason=request.reason,
    )
    if runtime_space is None:
        raise HTTPException(status_code=404, detail="Runtime space not found")
    return AdminQuarantineRuntimeSpaceResponse(
        id=runtime_space.id,
        created_at=runtime_space.created_at,
        updated_at=runtime_space.updated_at,
        workspace_id=runtime_space.workspace_id,
        name=runtime_space.name,
        scope=runtime_space.scope,
        status=runtime_space.status,
        reason=request.reason,
    )


@router.get("/worker-leases", response_model=PageResponse[AdminWorkerLeaseResponse])
async def list_admin_worker_leases(
    page: PageParams = Depends(pagination_params),
    status: str | None = Query(default=None),
    workspace_id: UUID | None = Query(default=None),
    worker_id: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminWorkerLeaseResponse]:
    items, total = AdminControlPlaneService(session).list_worker_leases(
        page,
        status=status,
        workspace_id=workspace_id,
        worker_id=worker_id,
    )
    return PageResponse(
        items=[AdminWorkerLeaseResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/queues/{queue_name}/metrics", response_model=AdminQueueMetricsResponse)
async def admin_queue_metrics(
    queue_name: str,
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> AdminQueueMetricsResponse:
    metrics = AdminControlPlaneService(
        session,
        redis,
        RedisKeyBuilder(settings.redis_key_prefix),
    ).queue_metrics(queue_name)
    return AdminQueueMetricsResponse(**metrics.model_dump())


@router.get("/operations/summary", response_model=AdminOperationsSummaryResponse)
async def admin_operations_summary(
    queue_name: str = Query(default="agent_runs"),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> AdminOperationsSummaryResponse:
    return AdminOperationsSummaryResponse(
        **AdminControlPlaneService(
            session,
            redis,
            RedisKeyBuilder(settings.redis_key_prefix),
        ).operations_summary(queue_name)
    )


@router.get("/system/configuration", response_model=AdminSystemConfigurationResponse)
async def admin_system_configuration(
    settings: Settings = Depends(get_settings),
) -> AdminSystemConfigurationResponse:
    recommendation = recommend_runtime_resources()
    recommended_resources = recommendation.as_dict()
    configured_resources = {
        "database_pool_size": settings.database_pool_size,
        "database_max_overflow": settings.database_max_overflow,
        "blocking_thread_pool_workers": settings.blocking_thread_pool_workers,
        "redis_max_connections": settings.redis_max_connections,
    }
    resource_deltas = {
        key: configured_resources[key] - recommended_resources[key]
        for key in configured_resources
    }
    return AdminSystemConfigurationResponse(
        settings=settings.redacted_summary(),
        recommended_resources=recommended_resources,
        configured_resources=configured_resources,
        resource_deltas=resource_deltas,
        blocking_executor=blocking_executor_snapshot(settings).as_dict(),
        database_pool=database_pool_snapshot().as_dict(),
    )


@router.get("/queues/{queue_name}/dead-letter-jobs", response_model=AdminDeadLetterJobsResponse)
async def list_admin_dead_letter_jobs(
    queue_name: str,
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> AdminDeadLetterJobsResponse:
    items, total = AdminControlPlaneService(
        session,
        redis,
        RedisKeyBuilder(settings.redis_key_prefix),
    ).list_dead_letters(queue_name, limit)
    return AdminDeadLetterJobsResponse(items=items, total=total)


@router.post(
    "/queues/{queue_name}/dead-letter-jobs/{job_id}/requeue",
    response_model=AdminRequeueDeadLetterResponse,
)
async def requeue_admin_dead_letter_job(
    queue_name: str,
    job_id: UUID,
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> AdminRequeueDeadLetterResponse:
    job = AdminControlPlaneService(
        session,
        redis,
        RedisKeyBuilder(settings.redis_key_prefix),
    ).requeue_dead_letter(queue_name, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Dead-letter job not found")
    return AdminRequeueDeadLetterResponse(requeued=True, job=job)


@router.get("/runtimes", response_model=PageResponse[AdminWorkspaceRuntimeResponse])
async def list_admin_runtimes(
    page: PageParams = Depends(pagination_params),
    workspace_id: UUID | None = Query(default=None),
    runtime_space_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    connection_status: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminWorkspaceRuntimeResponse]:
    items, total = AdminControlPlaneService(session).list_runtimes(
        page,
        workspace_id=workspace_id,
        runtime_space_id=runtime_space_id,
        status=status,
        connection_status=connection_status,
    )
    return PageResponse(
        items=[AdminWorkspaceRuntimeResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("/runtimes/{runtime_id}/force-stop", response_model=AdminWorkspaceRuntimeResponse)
async def force_stop_admin_runtime(
    runtime_id: UUID,
    request: AdminForceStopRuntimeRequest,
    session: Session = Depends(get_db_session),
) -> AdminWorkspaceRuntimeResponse:
    runtime = AdminControlPlaneService(session).force_stop_runtime(
        runtime_id,
        reason=request.reason,
    )
    if runtime is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    return AdminWorkspaceRuntimeResponse.model_validate(runtime)


@router.get("/platform-policies", response_model=PageResponse[AdminPlatformPolicyResponse])
async def list_admin_platform_policies(
    page: PageParams = Depends(pagination_params),
    status: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminPlatformPolicyResponse]:
    items, total = AdminControlPlaneService(session).list_platform_policies(page, status=status)
    return PageResponse(
        items=[AdminPlatformPolicyResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/platform-policies/risky-execution",
    response_model=AdminPlatformPolicyResponse,
)
async def get_admin_risky_execution_policy(
    session: Session = Depends(get_db_session),
) -> AdminPlatformPolicyResponse:
    policy = AdminControlPlaneService(session).get_or_create_risky_execution_policy()
    return AdminPlatformPolicyResponse.model_validate(policy)


@router.patch(
    "/platform-policies/risky-execution",
    response_model=AdminPlatformPolicyResponse,
)
async def update_admin_risky_execution_policy(
    request: AdminRiskyExecutionPolicyUpdateRequest,
    session: Session = Depends(get_db_session),
) -> AdminPlatformPolicyResponse:
    policy = AdminControlPlaneService(session).update_risky_execution_policy(
        value=request.value,
        updated_by=request.updated_by,
        description=request.description,
    )
    return AdminPlatformPolicyResponse.model_validate(policy)


@router.get("/security-events", response_model=PageResponse[AdminSecurityEventResponse])
async def list_admin_security_events(
    page: PageParams = Depends(pagination_params),
    severity: str | None = Query(default=None),
    workspace_id: UUID | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminSecurityEventResponse]:
    items, total = AdminControlPlaneService(session).list_security_events(
        page,
        severity=severity,
        workspace_id=workspace_id,
    )
    return PageResponse(
        items=[AdminSecurityEventResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )
