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
from backend.app.api.schemas.agents import AgentProfileCreateRequest, AgentProfileResponse
from backend.app.api.schemas.audit import AuditEventResponse
from backend.app.api.schemas.runs import AgentRunResponse, RunEventResponse
from backend.app.api.schemas.tasks import (
    TaskCorrectionDiagnosticsResponse,
    TaskCorrectionRequest,
    TaskCorrectionResponse,
    TaskCreateRequest,
    TaskExecutionDiagnosticsResponse,
    TaskHandoffQueueResponse,
    TaskLiveStatusResponse,
    TaskManagerDiagnosticsResponse,
    TaskManagerQueueResponse,
    TaskMessageResponse,
    TaskObservationResponse,
    TaskOperatorActionRequest,
    TaskOperatorActionResponse,
    TaskPlanDiagnosticsResponse,
    TaskPlanningAttemptResponse,
    TaskPlanRegenerateRequest,
    TaskPlanRetryRequest,
    TaskResponse,
    TaskTimelineResponse,
)
from backend.app.api.schemas.teams import (
    AgentTeamCommandCenterApplyRequest,
    AgentTeamCommandCenterApplyResponse,
    AgentTeamCommandCenterResponse,
    AgentTeamCreateRequest,
    AgentTeamExecutionLoopFinalizeRequest,
    AgentTeamExecutionLoopFinalizeResponse,
    AgentTeamExecutionLoopRunRequest,
    AgentTeamExecutionLoopRunResponse,
    AgentTeamExecutionOverviewResponse,
    AgentTeamMemberCreateRequest,
    AgentTeamMemberResponse,
    AgentTeamMemberUpdateRequest,
    AgentTeamOperatorActionRequest,
    AgentTeamOperatorActionResponse,
    AgentTeamOrgChartResponse,
    AgentTeamResponse,
)
from backend.app.api.services.resources import WorkspaceResourceService
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.planning.diagnostics import ProjectPlanDiagnosticsService
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.tasks.correction_diagnostics import TaskCorrectionDiagnosticsService
from backend.app.tasks.corrections import TaskCorrectionService
from backend.app.tasks.execution_diagnostics import TaskExecutionDiagnosticsService
from backend.app.tasks.live_status import TaskLiveStatusService
from backend.app.tasks.manager_diagnostics import TaskManagerDiagnosticsService
from backend.app.tasks.observation import TaskObservationService
from backend.app.tasks.operator_actions import TaskOperatorActionService
from backend.app.tasks.timeline import TaskTimelineService
from backend.app.teams.command_center import TeamCommandCenterService
from backend.app.teams.execution_loop import TeamExecutionLoopService
from backend.app.teams.execution_overview import TeamExecutionOverviewService
from backend.app.teams.operator_actions import TeamOperatorActionService
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
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> AgentProfileResponse:
    resource_service = WorkspaceResourceService(session, settings)
    idempotency = IdempotencyService(redis, RedisKeyBuilder(settings.redis_key_prefix))
    try:
        agent = run_idempotent_create(
            idempotency=idempotency,
            scope_id=context.workspace.id,
            operation="agents.create",
            idempotency_key=idempotency_key,
            get_existing=lambda agent_id: resource_service.get_agent(
                context.workspace.id,
                agent_id,
            ),
            create=lambda: resource_service.create_agent(
                context.workspace.id,
                request,
                context.user.user_id,
            ),
            resource_id=lambda created_agent: created_agent.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except IdempotencyInProgressError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request with this Idempotency-Key is still processing",
        ) from exc
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
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> AgentTeamResponse:
    resource_service = WorkspaceResourceService(session, settings)
    idempotency = IdempotencyService(redis, RedisKeyBuilder(settings.redis_key_prefix))
    try:
        team = run_idempotent_create(
            idempotency=idempotency,
            scope_id=context.workspace.id,
            operation="teams.create",
            idempotency_key=idempotency_key,
            get_existing=lambda team_id: resource_service.get_team(context.workspace.id, team_id),
            create=lambda: resource_service.create_team(
                context.workspace.id,
                request,
                context.user.user_id,
            ),
            resource_id=lambda created_team: created_team.id,
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
    return AgentTeamResponse.model_validate(team)


@router.get("/teams/{team_id}/org-chart", response_model=AgentTeamOrgChartResponse)
async def get_team_org_chart(
    team_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> AgentTeamOrgChartResponse:
    org_chart = WorkspaceResourceService(session).get_team_org_chart(
        context.workspace.id,
        team_id,
    )
    if org_chart is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamOrgChartResponse.model_validate(org_chart)


@router.get(
    "/teams/{team_id}/execution-overview",
    response_model=AgentTeamExecutionOverviewResponse,
)
async def get_team_execution_overview(
    team_id: UUID,
    include_completed: bool = Query(default=False),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> AgentTeamExecutionOverviewResponse:
    overview = TeamExecutionOverviewService(session).get_overview(
        workspace_id=context.workspace.id,
        team_id=team_id,
        include_completed=include_completed,
    )
    if overview is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamExecutionOverviewResponse.model_validate(overview)


@router.get(
    "/teams/{team_id}/command-center",
    response_model=AgentTeamCommandCenterResponse,
)
async def get_team_command_center(
    team_id: UUID,
    include_completed: bool = Query(default=False),
    queue_limit: int = Query(default=50, ge=1, le=200),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> AgentTeamCommandCenterResponse:
    command_center = TeamCommandCenterService(session).get_command_center(
        workspace_id=context.workspace.id,
        team_id=team_id,
        include_completed=include_completed,
        queue_limit=queue_limit,
    )
    if command_center is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamCommandCenterResponse.model_validate(command_center)


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
) -> AgentTeamCommandCenterApplyResponse:
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
        reason=request.reason,
        metadata=request.metadata,
    )
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamCommandCenterApplyResponse.model_validate(response)


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
) -> AgentTeamExecutionLoopRunResponse:
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
        reason=request.reason,
        metadata=request.metadata,
    )
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamExecutionLoopRunResponse.model_validate(response)


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


@router.get("/teams/{team_id}/members", response_model=PageResponse[AgentTeamMemberResponse])
async def list_team_members(
    team_id: UUID,
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentTeamMemberResponse]:
    try:
        items, total = WorkspaceResourceService(session).list_team_members(
            context.workspace.id,
            team_id,
            page,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "/teams/{team_id}/members",
    response_model=AgentTeamMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_team_member(
    team_id: UUID,
    request: AgentTeamMemberCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> AgentTeamMemberResponse:
    resource_service = WorkspaceResourceService(session, settings)
    idempotency = IdempotencyService(redis, RedisKeyBuilder(settings.redis_key_prefix))
    try:
        member = run_idempotent_create(
            idempotency=idempotency,
            scope_id=context.workspace.id,
            operation=f"teams.{team_id}.members.create",
            idempotency_key=idempotency_key,
            get_existing=lambda member_id: resource_service.get_team_member(
                context.workspace.id,
                team_id,
                member_id,
            ),
            create=lambda: resource_service.create_team_member(
                context.workspace.id,
                team_id,
                request,
                context.user.user_id,
            ),
            resource_id=lambda created_member: created_member.id,
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
    return AgentTeamMemberResponse.model_validate(member)


@router.patch(
    "/teams/{team_id}/members/{member_id}",
    response_model=AgentTeamMemberResponse,
)
async def update_team_member(
    team_id: UUID,
    member_id: UUID,
    request: AgentTeamMemberUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AgentTeamMemberResponse:
    try:
        member = WorkspaceResourceService(session, settings).update_team_member(
            context.workspace.id,
            team_id,
            member_id,
            request,
            context.user.user_id,
        )
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=message) from exc
    return AgentTeamMemberResponse.model_validate(member)


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
    queue: RedisQueue = Depends(get_worker_queue),
    settings: Settings = Depends(get_settings),
) -> TaskResponse:
    resource_service = WorkspaceResourceService(session, settings)
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
            get_existing=lambda task_id: resource_service.get_task(context.workspace.id, task_id),
            create=lambda: resource_service.create_task(
                workspace_id=context.workspace.id,
                created_by_user_id=context.user.user_id,
                data=request,
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


@router.post("/tasks/{task_id}/plan/retry", response_model=TaskResponse)
async def retry_task_plan(
    task_id: UUID,
    request: TaskPlanRetryRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
    settings: Settings = Depends(get_settings),
) -> TaskResponse:
    try:
        task = WorkspaceResourceService(session, settings).retry_task_plan(
            workspace_id=context.workspace.id,
            task_id=task_id,
            actor_user_id=context.user.user_id,
            data=request,
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
    settings: Settings = Depends(get_settings),
) -> TaskResponse:
    try:
        task = WorkspaceResourceService(session, settings).regenerate_task_plan(
            workspace_id=context.workspace.id,
            task_id=task_id,
            actor_user_id=context.user.user_id,
            data=request,
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


@router.get("/tasks/{task_id}/messages", response_model=PageResponse[TaskMessageResponse])
async def list_task_messages(
    task_id: UUID,
    page: PageParams = Depends(pagination_params),
    message_type: str | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[TaskMessageResponse]:
    try:
        items, total = WorkspaceResourceService(session).list_task_messages(
            workspace_id=context.workspace.id,
            task_id=task_id,
            page=page,
            message_type=message_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/tasks/{task_id}/live-status", response_model=TaskLiveStatusResponse)
async def get_task_live_status(
    task_id: UUID,
    after_sequence: int = Query(default=0, ge=0),
    message_limit: int = Query(default=50, ge=1, le=200),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TaskLiveStatusResponse:
    status_snapshot = TaskLiveStatusService(session).get_status(
        workspace_id=context.workspace.id,
        task_id=task_id,
        after_sequence=after_sequence,
        message_limit=message_limit,
    )
    if status_snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskLiveStatusResponse.model_validate(status_snapshot)


@router.get(
    "/tasks/{task_id}/planning-attempts",
    response_model=PageResponse[TaskPlanningAttemptResponse],
)
async def list_task_planning_attempts(
    task_id: UUID,
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[TaskPlanningAttemptResponse]:
    try:
        items, total = WorkspaceResourceService(session).list_task_planning_attempts(
            workspace_id=context.workspace.id,
            task_id=task_id,
            page=page,
            status=status_filter,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PageResponse(
        items=[TaskPlanningAttemptResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


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
