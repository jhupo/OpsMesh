from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import get_db_session
from opsmesh.teams.management.http_common import (
    _runtime_control,
)
from opsmesh.teams.sessions.schemas import (
    AgentTeamRuntimeBindRequest,
    AgentTeamRuntimeControlRequest,
    AgentTeamRuntimeEnsureRequest,
    AgentTeamRuntimeResponse,
)
from opsmesh.teams.sessions.service import TeamOperationService

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["workspace-resources"])


@router.get("/teams/{team_id}/runtime", response_model=AgentTeamRuntimeResponse)
def get_team_runtime(
    team_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> AgentTeamRuntimeResponse:
    state = TeamOperationService(session).get_or_initialize_state(
        workspace_id=context.workspace.id,
        team_id=team_id,
    )
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamRuntimeResponse.model_validate(state)


@router.post("/teams/{team_id}/runtime/start", response_model=AgentTeamRuntimeResponse)
def start_team_runtime(
    team_id: UUID,
    request: AgentTeamRuntimeControlRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AgentTeamRuntimeResponse:
    try:
        state = TeamOperationService(session).start(
            workspace_id=context.workspace.id,
            team_id=team_id,
            actor_user_id=context.user.user_id,
            runtime_control=_runtime_control(session, settings),
            reason=request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamRuntimeResponse.model_validate(state)


@router.post("/teams/{team_id}/runtime/pause", response_model=AgentTeamRuntimeResponse)
def pause_team_runtime(
    team_id: UUID,
    request: AgentTeamRuntimeControlRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AgentTeamRuntimeResponse:
    try:
        state = TeamOperationService(session).pause(
            workspace_id=context.workspace.id,
            team_id=team_id,
            actor_user_id=context.user.user_id,
            runtime_control=_runtime_control(session, settings),
            reason=request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamRuntimeResponse.model_validate(state)


@router.post("/teams/{team_id}/runtime/resume", response_model=AgentTeamRuntimeResponse)
def resume_team_runtime(
    team_id: UUID,
    request: AgentTeamRuntimeControlRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AgentTeamRuntimeResponse:
    try:
        state = TeamOperationService(session).resume(
            workspace_id=context.workspace.id,
            team_id=team_id,
            actor_user_id=context.user.user_id,
            runtime_control=_runtime_control(session, settings),
            reason=request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamRuntimeResponse.model_validate(state)


@router.post("/teams/{team_id}/runtime/stop", response_model=AgentTeamRuntimeResponse)
def stop_team_runtime(
    team_id: UUID,
    request: AgentTeamRuntimeControlRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AgentTeamRuntimeResponse:
    try:
        state = TeamOperationService(session).stop(
            workspace_id=context.workspace.id,
            team_id=team_id,
            actor_user_id=context.user.user_id,
            runtime_control=_runtime_control(session, settings),
            reason=request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamRuntimeResponse.model_validate(state)


@router.post("/teams/{team_id}/runtime/bind", response_model=AgentTeamRuntimeResponse)
def bind_team_runtime(
    team_id: UUID,
    request: AgentTeamRuntimeBindRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
) -> AgentTeamRuntimeResponse:
    try:
        state = TeamOperationService(session).bind_runtime(
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
def ensure_team_runtime(
    team_id: UUID,
    request: AgentTeamRuntimeEnsureRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AgentTeamRuntimeResponse:
    try:
        state = TeamOperationService(session).ensure_workspace_runtime(
            workspace_id=context.workspace.id,
            team_id=team_id,
            actor_user_id=context.user.user_id,
            runtime_control=_runtime_control(session, settings),
            template_id=request.template_id,
            name=request.name,
            start=request.start,
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


@router.post("/teams/{team_id}/runtime/continue", response_model=AgentTeamRuntimeResponse)
def continue_team_runtime(
    team_id: UUID,
    request: AgentTeamRuntimeControlRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_RUNTIME)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AgentTeamRuntimeResponse:
    try:
        state = TeamOperationService(session).continue_runtime(
            workspace_id=context.workspace.id,
            team_id=team_id,
            actor_user_id=context.user.user_id,
            runtime_control=_runtime_control(session, settings),
            instruction=request.instruction or request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamRuntimeResponse.model_validate(state)
