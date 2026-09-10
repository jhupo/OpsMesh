from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.api.schemas.operations import (
    BlockedStepExplanationResponse,
    BlockedStepUnblockRequest,
    BlockedStepUnblockResponse,
    OperationsSchedulerResponse,
    SchedulerControlResponse,
    SchedulerPauseRequest,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.pagination import PageParams
from backend.app.db.session import get_db_session
from backend.app.operations.scheduler_backlog import SchedulerBacklogService
from backend.app.operations.scheduler_blocked_steps import SchedulerBlockedStepService
from backend.app.operations.scheduler_control import SchedulerControlService
from backend.app.redis.cache import RedisJsonCache
from backend.app.redis.dependencies import get_cache_service

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis


router = APIRouter(prefix="/workspaces/{workspace_id}/operations", tags=["operations"])


@router.get("/scheduler", response_model=OperationsSchedulerResponse)
async def operations_scheduler(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    cache: RedisJsonCache = Depends(get_cache_service),
) -> OperationsSchedulerResponse:
    cache_key = f"scheduler:{context.workspace.id}"
    cached = cache.get_or_set(
        cache_key,
        lambda: (
            SchedulerBacklogService(session)
            .scheduler_payload(context.workspace.id)
            .model_dump(mode="json")
        ),
        ttl_seconds=10,
    )
    return OperationsSchedulerResponse.model_validate(cached.value)


@router.get("/blocked-steps", response_model=PageResponse[BlockedStepExplanationResponse])
async def operations_blocked_steps(
    page: PageParams = Depends(pagination_params),
    code: str | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> PageResponse[BlockedStepExplanationResponse]:
    items, total = SchedulerBlockedStepService(session).list_blocked_steps(
        context.workspace.id,
        page,
        code=code,
    )
    return PageResponse(
        items=items,
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("/blocked-steps/unblock", response_model=BlockedStepUnblockResponse)
async def unblock_blocked_steps(
    request: BlockedStepUnblockRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> BlockedStepUnblockResponse:
    try:
        return SchedulerBlockedStepService(session).unblock_steps(
            workspace_id=context.workspace.id,
            actor_user_id=context.user.user_id,
            code=request.code,
            reason=request.reason,
            runtime_space_id=request.runtime_space_id,
            limit=request.limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/scheduler/pause", response_model=SchedulerControlResponse)
async def pause_scheduler(
    request: SchedulerPauseRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> SchedulerControlResponse:
    response = SchedulerControlService(session).pause_scheduler(
        workspace_id=context.workspace.id,
        actor_user_id=context.user.user_id,
        reason=request.reason,
    )
    if response is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return response


@router.post("/scheduler/resume", response_model=SchedulerControlResponse)
async def resume_scheduler(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> SchedulerControlResponse:
    response = SchedulerControlService(session).resume_scheduler(
        workspace_id=context.workspace.id,
        actor_user_id=context.user.user_id,
    )
    if response is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return response
