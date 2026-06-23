from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.schemas.operations import (
    OperationsCapacityResponse,
    OperationsControlPlaneResponse,
    OperationsMcpJobsResponse,
    OperationsOutcomesResponse,
    OperationsOverviewResponse,
    OperationsRunActivityResponse,
    OperationsRuntimeCapacityResponse,
    OperationsSelfHostedMachinesResponse,
    OperationsWorkerLifecycleResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.operations.control_plane_service import OperationsControlPlaneService
from backend.app.operations.operation_capacity_payloads import OperationsCapacityPayloadService
from backend.app.operations.outcomes import OperationsOutcomeService
from backend.app.operations.overview_payloads import OperationsOverviewPayloadService
from backend.app.operations.run_activity_payloads import RunActivityPayloadService
from backend.app.operations.self_hosted_machine_payloads import (
    OperationsSelfHostedMachineService,
)
from backend.app.operations.worker_lifecycle_payloads import WorkerLifecyclePayloadService
from backend.app.redis.cache import RedisJsonCache
from backend.app.redis.dependencies import get_cache_service, get_redis_client
from backend.app.redis.keys import RedisKeyBuilder

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis


router = APIRouter(prefix="/workspaces/{workspace_id}/operations", tags=["operations"])


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
        lambda: (
            OperationsOverviewPayloadService(
                session,
                redis,
                RedisKeyBuilder(settings.redis_key_prefix),
            )
            .overview_payload(context.workspace.id, queue_name)
            .model_dump(mode="json")
        ),
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
        lambda: (
            OperationsControlPlaneService(
                session,
                redis,
                RedisKeyBuilder(settings.redis_key_prefix),
            )
            .control_plane_payload(
                context.workspace.id,
                queue_name,
                window_seconds=window_seconds,
            )
            .model_dump(mode="json")
        ),
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
        lambda: (
            OperationsCapacityPayloadService(
                session,
                redis,
                RedisKeyBuilder(settings.redis_key_prefix),
            )
            .capacity_payload(context.workspace.id, queue_name)
            .model_dump(mode="json")
        ),
        ttl_seconds=10,
    )
    return OperationsCapacityResponse(**cached.value)


@router.get("/runtime-capacity", response_model=OperationsRuntimeCapacityResponse)
async def operations_runtime_capacity(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    cache: RedisJsonCache = Depends(get_cache_service),
    settings: Settings = Depends(get_settings),
) -> OperationsRuntimeCapacityResponse:
    cache_key = f"runtime-capacity:{context.workspace.id}"
    cached = cache.get_or_set(
        cache_key,
        lambda: (
            OperationsCapacityPayloadService(
                session,
                redis,
                RedisKeyBuilder(settings.redis_key_prefix),
            )
            .runtime_capacity_payload(context.workspace.id)
            .model_dump(mode="json")
        ),
        ttl_seconds=10,
    )
    return OperationsRuntimeCapacityResponse(**cached.value)


@router.get("/worker-lifecycle", response_model=OperationsWorkerLifecycleResponse)
async def operations_worker_lifecycle(
    queue_name: str = Query(default="agent_runs"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    cache: RedisJsonCache = Depends(get_cache_service),
    settings: Settings = Depends(get_settings),
) -> OperationsWorkerLifecycleResponse:
    cache_key = f"worker-lifecycle:{context.workspace.id}:{queue_name}"
    cached = cache.get_or_set(
        cache_key,
        lambda: (
            WorkerLifecyclePayloadService(
                session,
                redis,
                RedisKeyBuilder(settings.redis_key_prefix),
            )
            .worker_lifecycle_payload(context.workspace.id, queue_name)
            .model_dump(mode="json")
        ),
        ttl_seconds=10,
    )
    return OperationsWorkerLifecycleResponse(**cached.value)


@router.get("/run-activity", response_model=OperationsRunActivityResponse)
async def operations_run_activity(
    team_id: UUID | None = Query(default=None),
    scan_limit: int = Query(default=500, ge=1, le=1_000),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    cache: RedisJsonCache = Depends(get_cache_service),
) -> OperationsRunActivityResponse:
    cache_key = f"run-activity:{context.workspace.id}:{team_id}:{scan_limit}"
    cached = cache.get_or_set(
        cache_key,
        lambda: (
            RunActivityPayloadService(session)
            .run_activity_payload(
                context.workspace.id,
                team_id=team_id,
                scan_limit=scan_limit,
            )
            .model_dump(mode="json")
        ),
        ttl_seconds=5,
    )
    return OperationsRunActivityResponse(**cached.value)


@router.get("/mcp-jobs", response_model=OperationsMcpJobsResponse)
async def operations_mcp_jobs(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    cache: RedisJsonCache = Depends(get_cache_service),
) -> OperationsMcpJobsResponse:
    cache_key = f"mcp-jobs:{context.workspace.id}"
    cached = cache.get_or_set(
        cache_key,
        lambda: (
            OperationsOutcomeService(session)
            .mcp_jobs_payload(context.workspace.id)
            .model_dump(mode="json")
        ),
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
        lambda: (
            OperationsSelfHostedMachineService(session)
            .self_hosted_machines_payload(
                context.workspace.id,
                stale_after_seconds=stale_after_seconds,
            )
            .model_dump(mode="json")
        ),
        ttl_seconds=10,
    )
    return OperationsSelfHostedMachinesResponse(**cached.value)


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
        lambda: (
            OperationsOutcomeService(session)
            .outcomes_payload(
                context.workspace.id,
                window_seconds=window_seconds,
            )
            .model_dump(mode="json")
        ),
        ttl_seconds=10,
    )
    return OperationsOutcomesResponse(**cached.value)
