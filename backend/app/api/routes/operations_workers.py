from __future__ import annotations

from hmac import compare_digest
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.operations import (
    RuntimeCleanupResponse,
    RuntimeLeaseResponse,
    WorkerHeartbeatRequest,
    WorkerHeartbeatResponse,
    WorkerLeaseResponse,
    WorkerNodeResponse,
    WorkerStatusUpdateRequest,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.operations.runtime_cleanup import RuntimeCleanupService
from backend.app.operations.runtime_leases import RuntimeLeaseOperationsService
from backend.app.operations.worker_heartbeats import WorkerHeartbeatOperationsService
from backend.app.operations.worker_lease_maintenance import WorkerLeaseMaintenanceService
from backend.app.operations.worker_lease_queries import WorkerLeaseQueryService
from backend.app.operations.worker_nodes import WorkerNodeOperationsService
from backend.app.security.service import SecurityAuditService

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis


router = APIRouter(prefix="/workspaces/{workspace_id}/operations", tags=["operations"])


@router.post("/worker-heartbeats", response_model=WorkerHeartbeatResponse)
async def record_worker_heartbeat(
    payload: WorkerHeartbeatRequest,
    request: Request,
    worker_heartbeat_token: str | None = Header(default=None, alias="X-Worker-Heartbeat-Token"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_db_session),
) -> WorkerHeartbeatResponse:
    if payload.workspace_id is not None and payload.workspace_id != context.workspace.id:
        raise HTTPException(status_code=400, detail="Heartbeat workspace_id must match path")
    _require_worker_heartbeat_token(
        request=request,
        context=context,
        settings=settings,
        session=session,
        presented_token=worker_heartbeat_token,
    )
    heartbeat = WorkerHeartbeatOperationsService(session).record_worker_heartbeat(
        workspace_id=context.workspace.id,
        worker_id=payload.worker_id,
        worker_type=payload.worker_type,
        status=payload.status,
        queue_name=payload.queue_name,
        details=payload.details,
        worker_version=payload.worker_version,
        hostname=payload.hostname,
        capacity=payload.capacity,
    )
    return WorkerHeartbeatResponse.model_validate(heartbeat)


def _require_worker_heartbeat_token(
    *,
    request: Request,
    context: WorkspaceContext,
    settings: Settings,
    session: Session,
    presented_token: str | None,
) -> None:
    expected_token = (settings.worker_heartbeat_token or "").strip()
    if not expected_token:
        return
    candidate = (presented_token or "").strip()
    if compare_digest(candidate, expected_token):
        return

    SecurityAuditService(session).record_request_event(
        request=request,
        action="worker.heartbeat_token.rejected",
        outcome="denied",
        severity="warning",
        reason="Invalid or missing worker heartbeat token",
        workspace_id=context.workspace.id,
        user_id=context.user.user_id,
        metadata={"has_heartbeat_header": bool(presented_token)},
    )
    session.commit()
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing worker heartbeat token",
    )


@router.get("/workers", response_model=PageResponse[WorkerNodeResponse])
async def list_workers(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    worker_type: str | None = Query(default=None),
    _: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> PageResponse[WorkerNodeResponse]:
    items, total = WorkerNodeOperationsService(session).list_worker_nodes(
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
    node = WorkerNodeOperationsService(session).request_worker_drain(worker_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Worker not found")
    return WorkerNodeResponse.model_validate(node)


@router.post("/workers/{worker_id}/status", response_model=WorkerNodeResponse)
async def update_worker_status(
    worker_id: str,
    request: WorkerStatusUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> WorkerNodeResponse:
    node = WorkerNodeOperationsService(session).set_worker_status(
        workspace_id=context.workspace.id,
        actor_user_id=context.user.user_id,
        worker_id=worker_id,
        status=request.status,
        reason=request.reason,
    )
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
    items, total = WorkerLeaseQueryService(session).list_worker_leases(
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
    items, total = RuntimeLeaseOperationsService(session).list_runtime_leases(
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


@router.post("/runtime-cleanup", response_model=RuntimeCleanupResponse)
async def cleanup_runtimes(
    stale_after_seconds: int = Query(default=600, ge=60, le=86_400),
    stale_lease_after_seconds: int = Query(default=900, ge=60, le=86_400),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
    session: Session = Depends(get_db_session),
) -> RuntimeCleanupResponse:
    stale, deleted = RuntimeCleanupService(session).cleanup_stale_runtimes(
        context.workspace.id,
        stale_after_seconds=stale_after_seconds,
    )
    expired_leases = WorkerLeaseMaintenanceService(session).expire_stale_worker_leases(
        workspace_id=context.workspace.id,
        stale_after_seconds=stale_lease_after_seconds,
    )
    return RuntimeCleanupResponse(
        stale_marked_offline=stale,
        deleted_records=deleted,
        expired_worker_leases=expired_leases,
    )
