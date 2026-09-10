from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.schemas.tasks import (
    TaskPlanDiagnosticsResponse,
    TaskPlanMutationRequest,
    TaskPlanRegenerateRequest,
    TaskPlanRetryRequest,
    TaskResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.db.session import get_db_session
from backend.app.planning.diagnostics import ProjectPlanDiagnosticsService
from backend.app.planning.future_plan_mutation import (
    TaskPlanMutationCommand,
    TaskPlanMutationError,
    TaskPlanMutationService,
)
from backend.app.tasks.plan_lifecycle import (
    TaskPlanLifecycleService,
    TaskPlanRegenerateCommand,
    TaskPlanRetryCommand,
)
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue.redis_queue import RedisQueue

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["workspace-resources"])
STREAM_TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}


@router.post("/tasks/{task_id}/plan/retry", response_model=TaskResponse)
async def retry_task_plan(
    task_id: UUID,
    request: TaskPlanRetryRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
) -> TaskResponse:
    try:
        task = TaskPlanLifecycleService(session).retry_task_plan(
            workspace_id=context.workspace.id,
            task_id=task_id,
            actor_user_id=context.user.user_id,
            command=TaskPlanRetryCommand(
                input=request.input,
                refresh_team_snapshot=request.refresh_team_snapshot,
            ),
            enqueue_run=request.enqueue and queue is not None,
            queue=queue,
        )
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=message) from exc
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskResponse.model_validate(task)


@router.post("/tasks/{task_id}/plan/regenerate", response_model=TaskResponse)
async def regenerate_task_plan(
    task_id: UUID,
    request: TaskPlanRegenerateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
) -> TaskResponse:
    try:
        task = TaskPlanLifecycleService(session).regenerate_task_plan(
            workspace_id=context.workspace.id,
            task_id=task_id,
            actor_user_id=context.user.user_id,
            command=TaskPlanRegenerateCommand(
                input=request.input,
                refresh_team_snapshot=request.refresh_team_snapshot,
            ),
            enqueue_run=request.enqueue and queue is not None,
            queue=queue,
        )
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=message) from exc
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskResponse.model_validate(task)


@router.post("/tasks/{task_id}/plan/mutate", response_model=TaskResponse)
async def mutate_task_plan(
    task_id: UUID,
    request: TaskPlanMutationRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
) -> TaskResponse:
    try:
        task = TaskPlanMutationService(session).apply(
            workspace_id=context.workspace.id,
            task_id=task_id,
            actor_user_id=context.user.user_id,
            command=TaskPlanMutationCommand(
                operations=tuple(
                    operation.model_dump(mode="json") for operation in request.operations
                ),
                reason=request.reason,
                refresh_team_snapshot=request.refresh_team_snapshot,
                mutation_id=request.mutation_id,
            ),
            enqueue_run=request.enqueue and queue is not None,
            queue=queue,
        )
    except TaskPlanMutationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=message) from exc
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskResponse.model_validate(task)


@router.get("/tasks/{task_id}/plan/diagnostics", response_model=TaskPlanDiagnosticsResponse)
async def get_task_plan_diagnostics(
    task_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TaskPlanDiagnosticsResponse:
    diagnostics = ProjectPlanDiagnosticsService(session).get_diagnostics(
        workspace_id=context.workspace.id,
        task_id=task_id,
    )
    if diagnostics is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskPlanDiagnosticsResponse.model_validate(diagnostics)
