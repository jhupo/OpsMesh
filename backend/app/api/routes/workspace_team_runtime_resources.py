from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.routes.workspace_team_common import (
    _enqueue_team_runtime_control,
    _queued_runtime_control,
    _team_runtime_limits,
)
from backend.app.api.schemas.teams import (
    AgentTeamRuntimeBindRequest,
    AgentTeamRuntimeControlRequest,
    AgentTeamRuntimeEnsureRequest,
    AgentTeamRuntimeResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.runtime_manager.quotas import RuntimeQuotaExceededError
from backend.app.runtime_manager.safety import RuntimeSafetyError
from backend.app.teams.runtime import TeamRuntimeService
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue import RedisQueue

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["workspace-resources"])

@router.get("/teams/{team_id}/runtime", response_model=AgentTeamRuntimeResponse)
async def get_team_runtime(
    team_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> AgentTeamRuntimeResponse:
    state = TeamRuntimeService(session).get_state(
        workspace_id=context.workspace.id,
        team_id=team_id,
    )
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamRuntimeResponse.model_validate(state)


@router.post("/teams/{team_id}/runtime/start", response_model=AgentTeamRuntimeResponse)
async def start_team_runtime(
    team_id: UUID,
    request: AgentTeamRuntimeControlRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
    settings: Settings = Depends(get_settings),
) -> AgentTeamRuntimeResponse:
    try:
        state = TeamRuntimeService(session).start(
            workspace_id=context.workspace.id,
            team_id=team_id,
            actor_user_id=context.user.user_id,
            runtime_control=_queued_runtime_control(session, queue, settings, context),
            reason=request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    _enqueue_team_runtime_control(queue, context, team_id, request, "start")
    return AgentTeamRuntimeResponse.model_validate(state)


@router.post("/teams/{team_id}/runtime/pause", response_model=AgentTeamRuntimeResponse)
async def pause_team_runtime(
    team_id: UUID,
    request: AgentTeamRuntimeControlRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
    settings: Settings = Depends(get_settings),
) -> AgentTeamRuntimeResponse:
    try:
        state = TeamRuntimeService(session).pause(
            workspace_id=context.workspace.id,
            team_id=team_id,
            actor_user_id=context.user.user_id,
            runtime_control=_queued_runtime_control(session, queue, settings, context),
            reason=request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    _enqueue_team_runtime_control(queue, context, team_id, request, "pause")
    return AgentTeamRuntimeResponse.model_validate(state)


@router.post("/teams/{team_id}/runtime/resume", response_model=AgentTeamRuntimeResponse)
async def resume_team_runtime(
    team_id: UUID,
    request: AgentTeamRuntimeControlRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
    settings: Settings = Depends(get_settings),
) -> AgentTeamRuntimeResponse:
    try:
        state = TeamRuntimeService(session).resume(
            workspace_id=context.workspace.id,
            team_id=team_id,
            actor_user_id=context.user.user_id,
            runtime_control=_queued_runtime_control(session, queue, settings, context),
            reason=request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    _enqueue_team_runtime_control(queue, context, team_id, request, "resume")
    return AgentTeamRuntimeResponse.model_validate(state)


@router.post("/teams/{team_id}/runtime/stop", response_model=AgentTeamRuntimeResponse)
async def stop_team_runtime(
    team_id: UUID,
    request: AgentTeamRuntimeControlRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
    settings: Settings = Depends(get_settings),
) -> AgentTeamRuntimeResponse:
    try:
        state = TeamRuntimeService(session).stop(
            workspace_id=context.workspace.id,
            team_id=team_id,
            actor_user_id=context.user.user_id,
            runtime_control=_queued_runtime_control(session, queue, settings, context),
            reason=request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    _enqueue_team_runtime_control(queue, context, team_id, request, "stop")
    return AgentTeamRuntimeResponse.model_validate(state)


@router.post("/teams/{team_id}/runtime/bind", response_model=AgentTeamRuntimeResponse)
async def bind_team_runtime(
    team_id: UUID,
    request: AgentTeamRuntimeBindRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
) -> AgentTeamRuntimeResponse:
    try:
        state = TeamRuntimeService(session).bind_runtime(
            workspace_id=context.workspace.id,
            team_id=team_id,
            workspace_runtime_id=request.workspace_runtime_id,
            actor_user_id=context.user.user_id,
            reason=request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=message) from exc
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamRuntimeResponse.model_validate(state)


@router.post("/teams/{team_id}/runtime/ensure", response_model=AgentTeamRuntimeResponse)
async def ensure_team_runtime(
    team_id: UUID,
    request: AgentTeamRuntimeEnsureRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
    settings: Settings = Depends(get_settings),
) -> AgentTeamRuntimeResponse:
    try:
        state = TeamRuntimeService(session).ensure_workspace_runtime(
            workspace_id=context.workspace.id,
            team_id=team_id,
            actor_user_id=context.user.user_id,
            runtime_control=_queued_runtime_control(session, queue, settings, context),
            template_id=request.template_id,
            name=request.name,
            limits=_team_runtime_limits(request),
            network_disabled=request.network_disabled,
            start=request.start,
            reason=request.reason,
            metadata=request.metadata,
        )
    except RuntimeSafetyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc
    except RuntimeQuotaExceededError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=message) from exc
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    _enqueue_team_runtime_control(queue, context, team_id, request, "ensure")
    return AgentTeamRuntimeResponse.model_validate(state)


@router.post("/teams/{team_id}/runtime/continue", response_model=AgentTeamRuntimeResponse)
async def continue_team_runtime(
    team_id: UUID,
    request: AgentTeamRuntimeControlRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
    settings: Settings = Depends(get_settings),
) -> AgentTeamRuntimeResponse:
    try:
        state = TeamRuntimeService(session).continue_runtime(
            workspace_id=context.workspace.id,
            team_id=team_id,
            actor_user_id=context.user.user_id,
            runtime_control=_queued_runtime_control(session, queue, settings, context),
            instruction=request.instruction or request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    _enqueue_team_runtime_control(queue, context, team_id, request, "continue")
    return AgentTeamRuntimeResponse.model_validate(state)


