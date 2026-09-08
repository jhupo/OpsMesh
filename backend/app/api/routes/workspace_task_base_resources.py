from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.idempotency import (
    IdempotencyInProgressError,
    IdempotencyService,
    run_idempotent_create,
)
from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.tasks import (
    TaskCreateRequest,
    TaskHandoffQueueResponse,
    TaskManagerQueueResponse,
    TaskResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.orchestration.run_control import RunControlService
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.tasks.events import RedisTaskEventBus, TaskEventBus
from backend.app.tasks.execution_diagnostics import TaskExecutionDiagnosticsService
from backend.app.tasks.manager_diagnostics import TaskManagerDiagnosticsService
from backend.app.tasks.workspace_service import (
    TaskCreateCommand,
    WorkspaceTaskService,
)
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue.redis_queue import RedisQueue

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["workspace-resources"])
STREAM_TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}


def get_task_event_bus(
    redis: Redis = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> TaskEventBus:
    return RedisTaskEventBus(redis=redis, key_prefix=settings.redis_key_prefix)


@router.get("/tasks", response_model=PageResponse[TaskResponse])
async def list_tasks(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[TaskResponse]:
    items, total = WorkspaceTaskService(session).list_tasks(
        context.workspace.id,
        page,
        status_filter,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    request: TaskCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    queue: RedisQueue = Depends(get_worker_queue),
    settings: Settings = Depends(get_settings),
) -> TaskResponse:
    task_service = WorkspaceTaskService(session)
    idempotency = IdempotencyService(
        redis,
        RedisKeyBuilder(settings.redis_key_prefix),
    )
    try:
        task = run_idempotent_create(
            idempotency=idempotency,
            scope_id=context.workspace.id,
            operation="tasks.create",
            idempotency_key=idempotency_key,
            get_existing=lambda task_id: task_service.get_task(context.workspace.id, task_id),
            create=lambda: task_service.create_task(
                workspace_id=context.workspace.id,
                created_by_user_id=context.user.user_id,
                command=_task_create_command(request),
                queue=queue,
            ),
            resource_id=lambda created_task: created_task.id,
        )
    except IdempotencyInProgressError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request with this Idempotency-Key is still processing",
        ) from exc
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=message) from exc
    return TaskResponse.model_validate(task)


@router.get("/tasks/manager-queue", response_model=TaskManagerQueueResponse)
async def list_task_manager_queue(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    team_id: UUID | None = Query(default=None),
    include_healthy: bool = Query(default=False),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TaskManagerQueueResponse:
    try:
        response = TaskManagerDiagnosticsService(session).list_manager_queue(
            workspace_id=context.workspace.id,
            limit=page.limit,
            offset=page.offset,
            status=status_filter,
            team_id=team_id,
            include_healthy=include_healthy,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return TaskManagerQueueResponse.model_validate(response)


@router.get("/tasks/handoff-queue", response_model=TaskHandoffQueueResponse)
async def list_task_handoff_queue(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    team_id: UUID | None = Query(default=None),
    handoff_status: str | None = Query(default=None),
    include_terminal: bool = Query(default=False),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TaskHandoffQueueResponse:
    try:
        response = TaskExecutionDiagnosticsService(session).list_handoff_queue(
            workspace_id=context.workspace.id,
            limit=page.limit,
            offset=page.offset,
            task_status=status_filter,
            team_id=team_id,
            handoff_status=handoff_status,
            include_terminal=include_terminal,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return TaskHandoffQueueResponse.model_validate(response)


@router.post("/tasks/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(
    task_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> TaskResponse:
    try:
        task = RunControlService(
            session=session,
            enqueue_run=RunOrchestrationService(session).enqueue_run,
        ).cancel_task(
            workspace_id=context.workspace.id,
            task_id=task_id,
            actor_user_id=context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskResponse.model_validate(task)


def _task_create_command(request: TaskCreateRequest) -> TaskCreateCommand:
    return TaskCreateCommand(
        agent_team_id=request.agent_team_id,
        runtime_space_id=request.runtime_space_id,
        workspace_project_id=request.workspace_project_id,
        domain_type=request.domain_type,
        title=request.title,
        description=request.description,
        priority=request.priority,
        input=request.input,
        generic_state=request.generic_state,
        domain_state=request.domain_state,
    )
