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
from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.api.schemas.teams import (
    AgentTeamCommandCenterResponse,
    AgentTeamCreateRequest,
    AgentTeamExecutionOverviewResponse,
    AgentTeamOperationsConsoleResponse,
    AgentTeamOrgChartResponse,
    AgentTeamProjectSpaceResponse,
    AgentTeamResponse,
    AgentTeamUpdateRequest,
    WorkspaceTeamCommandCenterResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.capabilities.policy_service import TeamCapabilityPolicyService
from backend.app.core.config import Settings, get_settings
from backend.app.core.pagination import PageParams
from backend.app.db.session import get_db_session
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.teams.command_center import TeamCommandCenterService
from backend.app.teams.execution_overview import TeamExecutionOverviewService
from backend.app.teams.models import AgentTeam
from backend.app.teams.operations_console import TeamOperationsConsoleService
from backend.app.teams.project_space import TeamProjectSpaceService
from backend.app.teams.workspace_command_center import WorkspaceCommandCenterService
from backend.app.teams.workspace_service import (
    TeamCreateCommand,
    TeamUpdateCommand,
    WorkspaceTeamService,
)
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue.redis_queue import RedisQueue

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["workspace-resources"])


@router.get("/teams", response_model=PageResponse[AgentTeamResponse])
async def list_teams(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentTeamResponse]:
    items, total = WorkspaceTeamService(session).list_teams(context.workspace.id, page)
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
    team_service = WorkspaceTeamService(session)
    idempotency = IdempotencyService(redis, RedisKeyBuilder(settings.redis_key_prefix))
    try:
        team = run_idempotent_create(
            idempotency=idempotency,
            scope_id=context.workspace.id,
            operation="teams.create",
            idempotency_key=idempotency_key,
            get_existing=lambda team_id: team_service.get_team(context.workspace.id, team_id),
            create=lambda: _create_validated_team(
                team_service=team_service,
                policy_service=TeamCapabilityPolicyService(session),
                workspace_id=context.workspace.id,
                request=request,
                actor_user_id=context.user.user_id,
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


@router.patch("/teams/{team_id}", response_model=AgentTeamResponse)
async def update_team(
    team_id: UUID,
    request: AgentTeamUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentTeamResponse:
    try:
        team = WorkspaceTeamService(session).update_team(
            context.workspace.id,
            team_id,
            TeamUpdateCommand(changes=request.model_dump(exclude_unset=True)),
            context.user.user_id,
        )
    except ValueError as exc:
        message = str(exc)
        error_status = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=error_status, detail=message) from exc
    return AgentTeamResponse.model_validate(team)


@router.get("/teams/{team_id}/org-chart", response_model=AgentTeamOrgChartResponse)
async def get_team_org_chart(
    team_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> AgentTeamOrgChartResponse:
    org_chart = WorkspaceTeamService(session).get_team_org_chart(
        context.workspace.id,
        team_id,
    )
    if org_chart is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamOrgChartResponse.model_validate(org_chart)


@router.get(
    "/teams/command-center",
    response_model=WorkspaceTeamCommandCenterResponse,
)
async def get_workspace_team_command_center(
    include_completed: bool = Query(default=False),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceTeamCommandCenterResponse:
    command_center = WorkspaceCommandCenterService(session).get_command_center(
        workspace_id=context.workspace.id,
        include_completed=include_completed,
    )
    return WorkspaceTeamCommandCenterResponse.model_validate(command_center)


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


@router.get(
    "/teams/{team_id}/project-space",
    response_model=AgentTeamProjectSpaceResponse,
)
async def get_team_project_space(
    team_id: UUID,
    include_completed: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> AgentTeamProjectSpaceResponse:
    project_space = TeamProjectSpaceService(session).get_project_space(
        workspace_id=context.workspace.id,
        team_id=team_id,
        include_completed=include_completed,
        limit=limit,
    )
    if project_space is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamProjectSpaceResponse.model_validate(project_space)


@router.get(
    "/teams/{team_id}/operations-console",
    response_model=AgentTeamOperationsConsoleResponse,
)
async def get_team_operations_console(
    team_id: UUID,
    include_completed: bool = Query(default=False),
    queue_limit: int = Query(default=50, ge=1, le=200),
    message_limit: int = Query(default=10, ge=0, le=50),
    session_limit: int = Query(default=100, ge=1, le=500),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
) -> AgentTeamOperationsConsoleResponse:
    console = TeamOperationsConsoleService(session).get_console(
        workspace_id=context.workspace.id,
        team_id=team_id,
        queue=queue,
        include_completed=include_completed,
        queue_limit=queue_limit,
        message_limit=message_limit,
        session_limit=session_limit,
    )
    if console is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return AgentTeamOperationsConsoleResponse.model_validate(console)


def _team_create_command(request: AgentTeamCreateRequest) -> TeamCreateCommand:
    return TeamCreateCommand(
        name=request.name,
        team_type=request.team_type,
        description=request.description,
        manager_agent_profile_id=request.manager_agent_profile_id,
        runtime_space_id=request.runtime_space_id,
        coordination_rules=request.coordination_rules,
        default_task_policy=request.default_task_policy,
        capability_policy=request.capability_policy.model_dump(mode="json"),
    )


def _create_validated_team(
    *,
    team_service: WorkspaceTeamService,
    policy_service: TeamCapabilityPolicyService,
    workspace_id: UUID,
    request: AgentTeamCreateRequest,
    actor_user_id: UUID,
) -> AgentTeam:
    policy_service.validate_policy(workspace_id, request.capability_policy)
    return team_service.create_team(
        workspace_id,
        _team_create_command(request),
        actor_user_id,
    )
