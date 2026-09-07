from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.audit import (
    AuditEventResponse,
    AuditIntegrityCheckResponse,
    AuditIntegrityStatusResponse,
    AuditIntegrityVerificationQueuedResponse,
)
from backend.app.api.schemas.operations import (
    AuditEventFilterResponse,
    FailedJobInspectionResponse,
    RunEventFilterResponse,
    RuntimeEventResponse,
    SecurityEventFilterResponse,
    SecurityEventResponse,
    StaleRunRecoverStatus,
    StaleRunRecoveryRequest,
    StaleRunRecoveryResponse,
    StaleRunsDiagnosticsResponse,
)
from backend.app.api.schemas.runs import AgentRunResponse, RunEventResponse
from backend.app.audit.integrity import AuditIntegrityService
from backend.app.audit.service import AuditService
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.operations.events import OperationsEventQueryService
from backend.app.operations.stale_run_diagnostics import StaleRunDiagnosticsService
from backend.app.operations.stale_run_recovery import StaleRunRecoveryService
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue.redis_queue import RedisQueue

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis


router = APIRouter(prefix="/workspaces/{workspace_id}/operations", tags=["operations"])


@router.get("/audit-integrity", response_model=AuditIntegrityStatusResponse)
async def audit_integrity_status(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AuditIntegrityStatusResponse:
    latest = AuditIntegrityService(session).latest(context.workspace.id)
    stale = latest is None or _as_utc(latest.created_at) < datetime.now(UTC) - timedelta(
        seconds=settings.audit_integrity_stale_after_seconds
    )
    return AuditIntegrityStatusResponse(
        status=(
            "missing"
            if latest is None
            else "invalid"
            if not latest.valid
            else "stale"
            if stale
            else "valid"
        ),
        stale=stale,
        latest=AuditIntegrityCheckResponse.model_validate(latest) if latest is not None else None,
    )


@router.post(
    "/audit-integrity/verify",
    response_model=AuditIntegrityVerificationQueuedResponse,
    status_code=202,
)
async def queue_audit_integrity_verification(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
) -> AuditIntegrityVerificationQueuedResponse:
    job = JobPayload(
        workspace_id=context.workspace.id,
        job_type=JobType.AUDIT_INTEGRITY_CHECK,
        resource_id=context.workspace.id,
        requested_by_user_id=context.user.user_id,
        idempotency_key=f"audit.integrity:{context.workspace.id}:{datetime.now(UTC).isoformat()}",
    )
    if not queue.enqueue(job, force=True):
        raise HTTPException(status_code=503, detail="Audit integrity verification was not queued")
    AuditService(session).record_user_action(
        workspace_id=context.workspace.id,
        user_id=context.user.user_id,
        action="audit.integrity_verification_queued",
        target_type="workspace",
        target_id=context.workspace.id,
        metadata={"job_id": str(job.job_id)},
    )
    session.commit()
    return AuditIntegrityVerificationQueuedResponse(job_id=job.job_id, status="queued")


@router.get("/run-events", response_model=RunEventFilterResponse)
async def list_run_events(
    page: PageParams = Depends(pagination_params),
    event_type: str | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> RunEventFilterResponse:
    items, total = OperationsEventQueryService(session).list_run_events(
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
    items, total = OperationsEventQueryService(session).list_runtime_events(
        context.workspace.id,
        page,
        runtime_id,
        event_type,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/stale-runs", response_model=StaleRunsDiagnosticsResponse)
async def stale_runs_diagnostics(
    stale_after_seconds: int = Query(default=900, ge=60, le=86_400),
    statuses: list[StaleRunRecoverStatus] | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> StaleRunsDiagnosticsResponse:
    try:
        return StaleRunDiagnosticsService(session).diagnostics(
            context.workspace.id,
            stale_after_seconds=stale_after_seconds,
            statuses=list(statuses) if statuses else None,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/stale-runs/recover", response_model=StaleRunRecoveryResponse)
async def recover_stale_runs(
    request: StaleRunRecoveryRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> StaleRunRecoveryResponse:
    try:
        return StaleRunRecoveryService(
            session,
            redis,
            RedisKeyBuilder(settings.redis_key_prefix),
        ).recover(
            context.workspace.id,
            actor_user_id=context.user.user_id,
            stale_after_seconds=request.stale_after_seconds,
            statuses=list(request.statuses),
            limit=request.limit,
            queue_name=request.queue_name,
            reason=request.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/failed-runs", response_model=FailedJobInspectionResponse)
async def inspect_failed_runs(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
    session: Session = Depends(get_db_session),
) -> FailedJobInspectionResponse:
    runs, total = OperationsEventQueryService(session).inspect_failed_runs(
        context.workspace.id,
        page,
    )
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
    items, total = OperationsEventQueryService(session).filter_audit_events(
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
    items, total = OperationsEventQueryService(session).filter_security_events(
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


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
