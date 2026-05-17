from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.idempotency import IdempotencyInProgressError, IdempotencyService
from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.agents import AgentProfileCreateRequest, AgentProfileResponse
from backend.app.api.schemas.audit import AuditEventResponse
from backend.app.api.schemas.runs import AgentRunResponse, RunEventResponse
from backend.app.api.schemas.tasks import TaskCreateRequest, TaskResponse
from backend.app.api.schemas.teams import AgentTeamCreateRequest, AgentTeamResponse
from backend.app.api.services.resources import WorkspaceResourceService
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue import RedisQueue

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["workspace-resources"])


@router.get("/agents", response_model=PageResponse[AgentProfileResponse])
async def list_agents(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentProfileResponse]:
    items, total = WorkspaceResourceService(session).list_agents(
        context.workspace.id,
        page,
        status_filter,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/agents", response_model=AgentProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    request: AgentProfileCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentProfileResponse:
    agent = WorkspaceResourceService(session).create_agent(
        context.workspace.id,
        request,
        context.user.user_id,
    )
    return AgentProfileResponse.model_validate(agent)


@router.get("/teams", response_model=PageResponse[AgentTeamResponse])
async def list_teams(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentTeamResponse]:
    items, total = WorkspaceResourceService(session).list_teams(context.workspace.id, page)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/teams", response_model=AgentTeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(
    request: AgentTeamCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentTeamResponse:
    team = WorkspaceResourceService(session).create_team(
        context.workspace.id,
        request,
        context.user.user_id,
    )
    return AgentTeamResponse.model_validate(team)


@router.get("/tasks", response_model=PageResponse[TaskResponse])
async def list_tasks(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[TaskResponse]:
    items, total = WorkspaceResourceService(session).list_tasks(
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
    settings: Settings = Depends(get_settings),
) -> TaskResponse:
    resource_service = WorkspaceResourceService(session)
    idempotency = IdempotencyService(
        redis,
        RedisKeyBuilder(settings.redis_key_prefix),
    )
    try:
        reservation = idempotency.reserve(
            workspace_id=context.workspace.id,
            operation="tasks.create",
            idempotency_key=idempotency_key,
        )
    except IdempotencyInProgressError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request with this Idempotency-Key is still processing",
        ) from exc

    if reservation is not None and reservation.existing_resource_id is not None:
        task = resource_service.get_task(context.workspace.id, reservation.existing_resource_id)
        if task is not None:
            return TaskResponse.model_validate(task)
        idempotency.forget(reservation)
        reservation = None

    try:
        task = resource_service.create_task(
            workspace_id=context.workspace.id,
            created_by_user_id=context.user.user_id,
            data=request,
        )
        idempotency.complete(reservation, task.id)
        return TaskResponse.model_validate(task)
    except Exception:
        idempotency.release(reservation)
        raise


@router.post("/tasks/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(
    task_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> TaskResponse:
    try:
        task = RunOrchestrationService(session).cancel_task(
            workspace_id=context.workspace.id,
            task_id=task_id,
            actor_user_id=context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskResponse.model_validate(task)


@router.get("/runs", response_model=PageResponse[AgentRunResponse])
async def list_runs(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentRunResponse]:
    items, total = WorkspaceResourceService(session).list_runs(
        context.workspace.id,
        page,
        status_filter,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/runs/{agent_run_id}/events", response_model=PageResponse[RunEventResponse])
async def list_run_events(
    agent_run_id: UUID,
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[RunEventResponse]:
    items, total = WorkspaceResourceService(session).list_run_events(
        context.workspace.id,
        agent_run_id,
        page,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/runs/{agent_run_id}/cancel", response_model=AgentRunResponse)
async def cancel_run(
    agent_run_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentRunResponse:
    try:
        run = RunOrchestrationService(session).cancel_run(
            workspace_id=context.workspace.id,
            run_id=agent_run_id,
            actor_user_id=context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    return AgentRunResponse.model_validate(run)


@router.post(
    "/runs/{agent_run_id}/retry",
    response_model=AgentRunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def retry_run(
    agent_run_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
) -> AgentRunResponse:
    try:
        run = RunOrchestrationService(session, queue=queue).retry_failed_run(
            workspace_id=context.workspace.id,
            run_id=agent_run_id,
            actor_user_id=context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    return AgentRunResponse.model_validate(run)


@router.get("/audit-events", response_model=PageResponse[AuditEventResponse])
async def list_audit_events(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AuditEventResponse]:
    items, total = WorkspaceResourceService(session).list_audit_events(context.workspace.id, page)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)
