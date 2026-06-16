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

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis


router = APIRouter(prefix="/workspaces/{workspace_id}/operations", tags=["operations"])


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
