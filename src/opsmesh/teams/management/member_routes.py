from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from redis import Redis
from sqlalchemy.orm import Session

from opsmesh.agents.profiles.contracts import AgentProfileResponse
from opsmesh.agents.profiles.http_errors import agent_management_http_error
from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.idempotency import (
    IdempotencyInProgressError,
    IdempotencyService,
    run_idempotent_create,
)
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.redis.dependencies import get_redis_client
from opsmesh.shared.redis.keys import RedisKeyBuilder
from opsmesh.teams.management.member_provider import (
    update_team_member_model_provider as change_team_member_model_provider,
)
from opsmesh.teams.management.schemas import (
    AgentTeamMemberCreateRequest,
    AgentTeamMemberResponse,
    AgentTeamMemberUpdateRequest,
)
from opsmesh.teams.management.service import (
    TeamMemberCreateCommand,
    TeamMemberUpdateCommand,
    WorkspaceTeamService,
)
from opsmesh.teams.operations.schemas import AgentTeamMemberModelProviderUpdateRequest

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["workspace-resources"])


@router.get("/teams/{team_id}/members", response_model=PageResponse[AgentTeamMemberResponse])
def list_team_members(
    team_id: UUID,
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentTeamMemberResponse]:
    try:
        items, total = WorkspaceTeamService(session).list_team_members(
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
def create_team_member(
    team_id: UUID,
    request: AgentTeamMemberCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> AgentTeamMemberResponse:
    team_service = WorkspaceTeamService(session)
    idempotency = IdempotencyService(redis, RedisKeyBuilder(settings.redis_key_prefix))
    try:
        member = run_idempotent_create(
            idempotency=idempotency,
            scope_id=context.workspace.id,
            operation=f"teams.{team_id}.members.create",
            idempotency_key=idempotency_key,
            get_existing=lambda member_id: team_service.get_team_member(
                context.workspace.id,
                team_id,
                member_id,
            ),
            create=lambda: team_service.create_team_member(
                context.workspace.id,
                team_id,
                _team_member_create_command(request),
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
def update_team_member(
    team_id: UUID,
    member_id: UUID,
    request: AgentTeamMemberUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentTeamMemberResponse:
    try:
        member = WorkspaceTeamService(session).update_team_member(
            context.workspace.id,
            team_id,
            member_id,
            TeamMemberUpdateCommand(changes=request.model_dump(exclude_unset=True)),
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


@router.post(
    "/teams/{team_id}/members/{member_id}/model-provider",
    response_model=AgentProfileResponse,
)
def update_team_member_model_provider(
    team_id: UUID,
    member_id: UUID,
    request: AgentTeamMemberModelProviderUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentProfileResponse:
    try:
        return change_team_member_model_provider(
            session,
            workspace_id=context.workspace.id,
            team_id=team_id,
            member_id=member_id,
            request=request,
            actor_user_id=context.user.user_id,
        )
    except ValueError as exc:
        raise agent_management_http_error(exc) from exc


def _team_member_create_command(
    request: AgentTeamMemberCreateRequest,
) -> TeamMemberCreateCommand:
    return TeamMemberCreateCommand(
        agent_profile_id=request.agent_profile_id,
        team_role=request.team_role,
        department=request.department,
        position_title=request.position_title,
        reports_to_member_id=request.reports_to_member_id,
        responsibilities=request.responsibilities,
        skill_weights=request.skill_weights,
        availability=request.availability,
        max_concurrent_tasks=request.max_concurrent_tasks,
        accepts_tasks=request.accepts_tasks,
        is_required=request.is_required,
        order_index=request.order_index,
    )
