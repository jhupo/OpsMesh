from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.orchestration import OrchestrationApplyRequest
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
from backend.app.orchestration.definitions import (
    OrchestrationDefinitionError,
    OrchestrationDefinitionService,
)
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.planning.diagnostics import ProjectPlanDiagnosticsService
from backend.app.planning.future_plan_mutation import (
    TaskPlanMutationCommand,
    TaskPlanMutationError,
    TaskPlanMutationService,
)
from backend.app.tasks.models import Task
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


@router.post("/tasks/{task_id}/plan/apply-orchestration", response_model=TaskResponse)
async def apply_orchestration_to_task(
    task_id: UUID,
    request: OrchestrationApplyRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
) -> TaskResponse:
    task = session.scalar(
        select(Task).where(
            Task.workspace_id == context.workspace.id,
            Task.id == task_id,
        )
    )
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    try:
        OrchestrationDefinitionService(session).apply_to_task(
            task,
            request.orchestration_definition_id,
            request.orchestration_version,
            context.user.user_id,
        )
        orchestration = RunOrchestrationService(session, queue=queue)
        run = orchestration.create_queued_run_for_task(task)
        if run is not None and request.enqueue and queue is not None:
            orchestration.enqueue_run(
                run,
                context.user.user_id,
            )
        session.commit()
        session.refresh(task)
    except OrchestrationDefinitionError as exc:
        session.rollback()
        code = (
            status.HTTP_404_NOT_FOUND
            if exc.code == "orchestration_not_found"
            else status.HTTP_409_CONFLICT
            if exc.code.startswith("orchestration_task_")
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(
            status_code=code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    return TaskResponse.model_validate(task)


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
                expected_revision=request.expected_revision,
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
