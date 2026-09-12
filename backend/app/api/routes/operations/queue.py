from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.schemas.operations.overview import (
    DeadLetterJobsResponse,
    OperationsQueueInsightsResponse,
    QueueGovernanceDiagnosticsResponse,
    QueueGovernanceReconcileRequest,
    QueueGovernanceReconcileResponse,
    QueueMetricsResponse,
    RequeueDeadLetterResponse,
)
from backend.app.core.auth.context import WorkspaceContext
from backend.app.core.auth.dependencies import workspace_dependency
from backend.app.core.auth.permissions import WorkspaceAction
from backend.app.core.common.config import Settings, get_settings
from backend.app.core.db.session import get_db_session
from backend.app.core.redis.dependencies import get_redis_client
from backend.app.core.redis.keys import RedisKeyBuilder
from backend.app.runtime.operations.queues.dead_letters import DeadLetterQueueService
from backend.app.runtime.operations.queues.governance import (
    QueueGovernanceDiagnosticsService,
    QueueGovernanceReconciliationService,
)
from backend.app.runtime.operations.queues.insights import QueueInsightsService
from backend.app.runtime.operations.queues.metrics import QueueMetricsService

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis


router = APIRouter(prefix="/workspaces/{workspace_id}/operations", tags=["operations"])


@router.get("/queue-metrics", response_model=QueueMetricsResponse)
async def queue_metrics(
    queue_name: str = Query(default="agent_runs"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> QueueMetricsResponse:
    return QueueMetricsService(
        redis,
        RedisKeyBuilder(settings.redis_key_prefix),
    ).queue_metrics(queue_name, context.workspace.id)


@router.get("/queue-insights", response_model=OperationsQueueInsightsResponse)
async def queue_insights(
    queue_name: str = Query(default="agent_runs"),
    scan_limit: int = Query(default=500, ge=1, le=5_000),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> OperationsQueueInsightsResponse:
    return QueueInsightsService(
        redis,
        RedisKeyBuilder(settings.redis_key_prefix),
    ).queue_insights(
        workspace_id=context.workspace.id,
        queue_name=queue_name,
        scan_limit=scan_limit,
    )


@router.get("/queue-governance", response_model=QueueGovernanceDiagnosticsResponse)
async def queue_governance(
    queue_name: str = Query(default="agent_runs"),
    scan_limit: int = Query(default=500, ge=1, le=5_000),
    stale_after_seconds: int = Query(default=900, ge=60, le=86_400),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> QueueGovernanceDiagnosticsResponse:
    return QueueGovernanceDiagnosticsService(
        session,
        redis,
        RedisKeyBuilder(settings.redis_key_prefix),
    ).queue_governance(
        workspace_id=context.workspace.id,
        queue_name=queue_name,
        scan_limit=scan_limit,
        stale_after_seconds=stale_after_seconds,
    )


@router.post(
    "/queue-governance/reconcile",
    response_model=QueueGovernanceReconcileResponse,
)
async def reconcile_queue_governance(
    request: QueueGovernanceReconcileRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> QueueGovernanceReconcileResponse:
    return QueueGovernanceReconciliationService(
        session,
        redis,
        RedisKeyBuilder(settings.redis_key_prefix),
    ).reconcile_queue_governance(
        workspace_id=context.workspace.id,
        actor_user_id=context.user.user_id,
        queue_name=request.queue_name,
        scan_limit=request.scan_limit,
        stale_after_seconds=request.stale_after_seconds,
        actions=list(request.actions),
        max_items=request.max_items,
        reason=request.reason,
    )


@router.get("/dead-letter-jobs", response_model=DeadLetterJobsResponse)
async def list_dead_letter_jobs(
    queue_name: str = Query(default="agent_runs"),
    limit: int = Query(default=50, ge=1, le=200),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> DeadLetterJobsResponse:
    return DeadLetterQueueService(
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
    job = DeadLetterQueueService(
        redis,
        RedisKeyBuilder(settings.redis_key_prefix),
    ).requeue_dead_letter(context.workspace.id, queue_name, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Dead-letter job not found")
    return RequeueDeadLetterResponse(requeued=True, job=job)
