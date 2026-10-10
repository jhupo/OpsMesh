from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query
from redis import Redis
from sqlalchemy.orm import Session

from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.orchestration.runs.scheduling.control import (
    SchedulerBlockedStepControlService,
    SchedulerControlService,
)
from opsmesh.runtime.operations.contracts.scheduler import (
    BlockedStepExplanationResponse,
    BlockedStepUnblockRequest,
    BlockedStepUnblockResponse,
    OperationsSchedulerResponse,
    SchedulerControlResponse,
    SchedulerPauseRequest,
)
from opsmesh.runtime.operations.scheduler import (
    SchedulerBacklogService,
    SchedulerBlockedStepService,
    SchedulerPolicyService,
)
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.redis.cache import RedisJsonCache
from opsmesh.shared.redis.dependencies import get_cache_service
from opsmesh.workspaces.management.settings import scheduler_settings

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis


router = APIRouter(prefix="/workspaces/{workspace_id}/operations", tags=["operations"])


@router.get("/scheduler", response_model=OperationsSchedulerResponse)
def operations_scheduler(
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
def operations_blocked_steps(
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
def unblock_blocked_steps(
    request: BlockedStepUnblockRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> BlockedStepUnblockResponse:
    try:
        unblocked = SchedulerBlockedStepControlService(session).unblock_steps(
            workspace_id=context.workspace.id,
            actor_user_id=context.user.user_id,
            code=request.code,
            reason=request.reason,
            runtime_space_id=request.runtime_space_id,
            limit=request.limit,
        )
        return BlockedStepUnblockResponse(
            workspace_id=context.workspace.id,
            unblocked_steps=unblocked,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/scheduler/pause", response_model=SchedulerControlResponse)
def pause_scheduler(
    request: SchedulerPauseRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> SchedulerControlResponse:
    result = SchedulerControlService(session).pause_scheduler(
        workspace_id=context.workspace.id,
        actor_user_id=context.user.user_id,
        reason=request.reason,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    workspace, cleared = result
    scheduler = scheduler_settings(workspace.settings)
    return SchedulerControlResponse(
        workspace_id=workspace.id,
        paused=True,
        pause_reason=str(scheduler["pause_reason"]),
        cleared_blocked_steps=cleared,
        policy=SchedulerPolicyService(session).scheduler_policy(workspace.id),
    )


@router.post("/scheduler/resume", response_model=SchedulerControlResponse)
def resume_scheduler(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> SchedulerControlResponse:
    result = SchedulerControlService(session).resume_scheduler(
        workspace_id=context.workspace.id,
        actor_user_id=context.user.user_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    workspace, cleared = result
    return SchedulerControlResponse(
        workspace_id=workspace.id,
        paused=False,
        pause_reason=None,
        cleared_blocked_steps=cleared,
        policy=SchedulerPolicyService(session).scheduler_policy(workspace.id),
    )
