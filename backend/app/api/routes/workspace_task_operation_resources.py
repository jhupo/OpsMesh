from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.schemas.tasks import (
    TaskCorrectionDiagnosticsResponse,
    TaskCorrectionRequest,
    TaskCorrectionResponse,
    TaskExecutionDiagnosticsResponse,
    TaskManagerDiagnosticsResponse,
    TaskObservationResponse,
    TaskOperatorActionRequest,
    TaskOperatorActionResponse,
    TaskTimelineResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.db.session import get_db_session
from backend.app.tasks.correction_diagnostics import TaskCorrectionDiagnosticsService
from backend.app.tasks.corrections import TaskCorrectionService
from backend.app.tasks.execution_diagnostics import TaskExecutionDiagnosticsService
from backend.app.tasks.manager_diagnostics import TaskManagerDiagnosticsService
from backend.app.tasks.observation import TaskObservationService
from backend.app.tasks.operator_actions import TaskOperatorActionService
from backend.app.tasks.timeline import TaskTimelineService

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["workspace-resources"])
STREAM_TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}


@router.post(
    "/tasks/{task_id}/corrections",
    response_model=TaskCorrectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_task_correction(
    task_id: UUID,
    request: TaskCorrectionRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> TaskCorrectionResponse:
    try:
        correction = TaskCorrectionService(session).create_correction(
            workspace_id=context.workspace.id,
            task_id=task_id,
            actor_user_id=context.user.user_id,
            request=request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if correction is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskCorrectionResponse(**correction.__dict__)


@router.get(
    "/tasks/{task_id}/corrections/diagnostics",
    response_model=TaskCorrectionDiagnosticsResponse,
)
async def get_task_correction_diagnostics(
    task_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TaskCorrectionDiagnosticsResponse:
    diagnostics = TaskCorrectionDiagnosticsService(session).get_diagnostics(
        workspace_id=context.workspace.id,
        task_id=task_id,
    )
    if diagnostics is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskCorrectionDiagnosticsResponse.model_validate(diagnostics)


@router.get("/tasks/{task_id}/observation", response_model=TaskObservationResponse)
async def get_task_observation(
    task_id: UUID,
    view_type: str | None = Query(default="auto"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TaskObservationResponse:
    try:
        observation = TaskObservationService(session).get_observation(
            workspace_id=context.workspace.id,
            task_id=task_id,
            view_type=view_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if observation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskObservationResponse.model_validate(observation)


@router.get("/tasks/{task_id}/timeline", response_model=TaskTimelineResponse)
async def get_task_timeline(
    task_id: UUID,
    limit: int = Query(default=200, ge=1, le=500),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TaskTimelineResponse:
    timeline = TaskTimelineService(session).get_timeline(
        workspace_id=context.workspace.id,
        task_id=task_id,
        limit=limit,
    )
    if timeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskTimelineResponse.model_validate(timeline)


@router.post("/tasks/{task_id}/operator-actions", response_model=TaskOperatorActionResponse)
async def apply_task_operator_action(
    task_id: UUID,
    request: TaskOperatorActionRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> TaskOperatorActionResponse:
    try:
        response = TaskOperatorActionService(session).apply_action(
            workspace_id=context.workspace.id,
            task_id=task_id,
            actor_user_id=context.user.user_id,
            action=request.action,
            task_step_ids=request.task_step_ids,
            agent_profile_id=request.agent_profile_id,
            instruction=request.instruction,
            reason=request.reason,
            metadata=request.metadata,
        )
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=message) from exc
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskOperatorActionResponse.model_validate(response)


@router.get(
    "/tasks/{task_id}/execution-diagnostics",
    response_model=TaskExecutionDiagnosticsResponse,
)
async def get_task_execution_diagnostics(
    task_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TaskExecutionDiagnosticsResponse:
    diagnostics = TaskExecutionDiagnosticsService(session).get_diagnostics(
        workspace_id=context.workspace.id,
        task_id=task_id,
    )
    if diagnostics is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskExecutionDiagnosticsResponse.model_validate(diagnostics)


@router.get(
    "/tasks/{task_id}/manager-diagnostics",
    response_model=TaskManagerDiagnosticsResponse,
)
async def get_task_manager_diagnostics(
    task_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TaskManagerDiagnosticsResponse:
    diagnostics = TaskManagerDiagnosticsService(session).get_diagnostics(
        workspace_id=context.workspace.id,
        task_id=task_id,
    )
    if diagnostics is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskManagerDiagnosticsResponse.model_validate(diagnostics)

