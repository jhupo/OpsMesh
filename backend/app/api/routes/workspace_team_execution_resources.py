from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.routes.workspace_team_common import _queued_runtime_control
from backend.app.api.schemas.redaction import redact_sensitive_payload
from backend.app.api.schemas.teams import (
    AgentTeamCommandCenterApplyRequest,
    AgentTeamCommandCenterApplyResponse,
    AgentTeamExecutionLoopEnqueueRequest,
    AgentTeamExecutionLoopEnqueueResponse,
    AgentTeamExecutionLoopFinalizeRequest,
    AgentTeamExecutionLoopFinalizeResponse,
    AgentTeamExecutionLoopRunRequest,
    AgentTeamExecutionLoopRunResponse,
    AgentTeamOperatorActionRequest,
    AgentTeamOperatorActionResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.errors import PermissionDeniedError
from backend.app.auth.permissions import WorkspaceAction
from backend.app.auth.service import AuthorizationService
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.teams.command_center import TeamCommandCenterService
from backend.app.teams.execution_loop import (
    TeamExecutionLoopService,
    enqueue_team_execution_loop_job,
)
from backend.app.teams.operator_actions import TeamOperatorActionService
from backend.app.teams.workspace_service import (
    WorkspaceTeamService,
)
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.jobs import JobType
from backend.app.workers.queue.redis_queue import RedisQueue

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["workspace-resources"])

@router.post(
    "/teams/{team_id}/command-center/actions/apply",
    response_model=AgentTeamCommandCenterApplyResponse,
)
async def apply_team_command_center_actions(
    team_id: UUID,
    request: AgentTeamCommandCenterApplyRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
    settings: Settings = Depends(get_settings),
) -> AgentTeamCommandCenterApplyResponse:
    _require_command_center_runtime_permission(
        session=session,
        context=context,
        request=request,
    )
    response = TeamCommandCenterService(session).apply_action_plan(
        workspace_id=context.workspace.id,
        team_id=team_id,
        actor_user_id=context.user.user_id,
        include_completed=request.include_completed,
        queue_limit=request.queue_limit,
        dry_run=request.dry_run,
        sources=request.sources or None,
        actions=request.actions or None,
        max_actions=request.max_actions,
        max_tasks_per_action=request.max_tasks_per_action,
        enqueue_runs=request.enqueue_runs,
        queue=queue,
        runtime_control=None
        if request.dry_run
        else _queued_runtime_control(session, queue, settings, context),
        reason=request.reason,
        metadata=request.metadata,
    )
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamCommandCenterApplyResponse.model_validate(response)


def _require_command_center_runtime_permission(
    *,
    session: Session,
    context: WorkspaceContext,
    request: AgentTeamCommandCenterApplyRequest,
) -> None:
    if request.dry_run:
        return
    requested_sources = set(request.sources or [])
    if requested_sources and "team_runtime" not in requested_sources:
        return
    requested_actions = set(request.actions or [])
    if requested_actions and not requested_actions.intersection(
        {"ensure_team_runtime", "start_team_runtime"}
    ):
        return
    try:
        AuthorizationService(session).require_workspace(
            user_id=context.user.user_id,
            workspace_id=context.workspace.id,
            action=WorkspaceAction.MANAGE_RUNTIME,
            authenticated_user=context.user,
        )
    except PermissionDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace role does not allow runtime lifecycle actions",
        ) from exc


@router.post(
    "/teams/{team_id}/execution-loop/enqueue",
    response_model=AgentTeamExecutionLoopEnqueueResponse,
)
async def enqueue_team_execution_loop(
    team_id: UUID,
    request: AgentTeamExecutionLoopEnqueueRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
) -> AgentTeamExecutionLoopEnqueueResponse:
    team = WorkspaceTeamService(session).get_team(context.workspace.id, team_id)
    if team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    queued = enqueue_team_execution_loop_job(
        queue=queue,
        workspace_id=context.workspace.id,
        team_id=team_id,
        requested_by_user_id=context.user.user_id,
        idempotency_suffix=request.idempotency_suffix,
        priority=request.priority,
        routing={
            "source": "workspace_api",
            "trigger": "manual_enqueue",
            "reason": request.reason,
            "metadata": redact_sensitive_payload(request.metadata),
        },
    )
    return AgentTeamExecutionLoopEnqueueResponse(
        workspace_id=context.workspace.id,
        team_id=team_id,
        status="queued" if queued else "skipped",
        queued=queued,
        job_type=JobType.TEAM_EXECUTION_LOOP.value,
        queue_name=queue.queue_name,
    )


@router.post(
    "/teams/{team_id}/execution-loop/run",
    response_model=AgentTeamExecutionLoopRunResponse,
)
async def run_team_execution_loop_iteration(
    team_id: UUID,
    request: AgentTeamExecutionLoopRunRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
    settings: Settings = Depends(get_settings),
) -> AgentTeamExecutionLoopRunResponse:
    _require_execution_loop_runtime_permission(
        session=session,
        context=context,
        request=request,
    )
    response = TeamExecutionLoopService(session).run_iteration(
        workspace_id=context.workspace.id,
        team_id=team_id,
        actor_user_id=context.user.user_id,
        dry_run=request.dry_run,
        apply_command_center_actions=request.apply_command_center_actions,
        enqueue_runs=request.enqueue_runs,
        finalize_ready_tasks=request.finalize_ready_tasks,
        include_completed=request.include_completed,
        queue_limit=request.queue_limit,
        sources=request.sources or None,
        actions=request.actions or None,
        max_actions=request.max_actions,
        max_tasks_per_action=request.max_tasks_per_action,
        max_finalize_tasks=request.max_finalize_tasks,
        queue=queue,
        runtime_control=None
        if request.dry_run
        else _queued_runtime_control(session, queue, settings, context),
        reason=request.reason,
        metadata=request.metadata,
    )
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamExecutionLoopRunResponse.model_validate(response)


def _require_execution_loop_runtime_permission(
    *,
    session: Session,
    context: WorkspaceContext,
    request: AgentTeamExecutionLoopRunRequest,
) -> None:
    if request.dry_run:
        return
    try:
        AuthorizationService(session).require_workspace(
            user_id=context.user.user_id,
            workspace_id=context.workspace.id,
            action=WorkspaceAction.MANAGE_RUNTIME,
            authenticated_user=context.user,
        )
    except PermissionDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace role does not allow runtime lifecycle actions",
        ) from exc


@router.post(
    "/teams/{team_id}/execution-loop/finalize",
    response_model=AgentTeamExecutionLoopFinalizeResponse,
)
async def finalize_team_execution_loop_tasks(
    team_id: UUID,
    request: AgentTeamExecutionLoopFinalizeRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentTeamExecutionLoopFinalizeResponse:
    response = TeamExecutionLoopService(session).finalize_ready_tasks(
        workspace_id=context.workspace.id,
        team_id=team_id,
        actor_user_id=context.user.user_id,
        dry_run=request.dry_run,
        max_tasks=request.max_tasks,
    )
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamExecutionLoopFinalizeResponse.model_validate(response)


@router.post(
    "/teams/{team_id}/operator-actions",
    response_model=AgentTeamOperatorActionResponse,
)
async def apply_team_operator_action(
    team_id: UUID,
    request: AgentTeamOperatorActionRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentTeamOperatorActionResponse:
    try:
        response = TeamOperatorActionService(session).apply_action(
            workspace_id=context.workspace.id,
            team_id=team_id,
            actor_user_id=context.user.user_id,
            action=request.action,
            task_ids=request.task_ids,
            task_step_ids=request.task_step_ids,
            agent_profile_id=request.agent_profile_id,
            max_tasks=request.max_tasks,
            instruction=request.instruction,
            reason=request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamOperatorActionResponse.model_validate(response)

