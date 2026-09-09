from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.agent_messages.models import AgentMessage
from backend.app.agent_runtime.session_management import (
    PersistentAgentSessionManagementService,
)
from backend.app.agents.model_provider_summary import agent_profile_response
from backend.app.agents.models import AgentProfile
from backend.app.agents.service import AgentManagementService
from backend.app.api.idempotency import (
    IdempotencyInProgressError,
    IdempotencyService,
    run_idempotent_create,
)
from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.routes.agent_errors import agent_management_http_error
from backend.app.api.schemas.agents import (
    AgentProfileResponse,
)
from backend.app.api.schemas.teams import (
    AgentTeamMemberCreateRequest,
    AgentTeamMemberModelProviderUpdateRequest,
    AgentTeamMemberResponse,
    AgentTeamMemberUpdateRequest,
)
from backend.app.audit.service import AuditService
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.model_providers.model_api import configured_model_api, require_known_model_api
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.teams.runtime import TeamRuntimeService
from backend.app.teams.workspace_service import (
    TeamMemberCreateCommand,
    TeamMemberUpdateCommand,
    WorkspaceTeamService,
)

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["workspace-resources"])


@router.get("/teams/{team_id}/members", response_model=PageResponse[AgentTeamMemberResponse])
async def list_team_members(
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
async def create_team_member(
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
async def update_team_member(
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
async def update_team_member_model_provider(
    team_id: UUID,
    member_id: UUID,
    request: AgentTeamMemberModelProviderUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
) -> AgentProfileResponse:
    member = WorkspaceTeamService(session).get_team_member(
        context.workspace.id,
        team_id,
        member_id,
    )
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team member not found")
    before_agent = AgentManagementService(session).get_agent(
        context.workspace.id,
        member.agent_profile_id,
    )
    if before_agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    before_provider_state = _team_member_model_provider_state(before_agent)
    request_fields = request.model_fields_set
    changes: dict[str, object] = {}
    if "model_provider_credential_id" in request_fields:
        changes["model_provider_credential_id"] = request.model_provider_credential_id
    if request.model is not None:
        changes["model"] = request.model
    if "model_api" in request_fields:
        model_settings = dict(before_agent.model_settings or {})
        model_api = require_known_model_api(request.model_api)
        if model_api is None:
            model_settings.pop("model_api", None)
        else:
            model_settings["model_api"] = model_api
        changes["model_settings"] = model_settings
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide model_provider_credential_id, model, or model_api to update",
        )
    try:
        agent = AgentManagementService(session).update_agent(
            context.workspace.id,
            member.agent_profile_id,
            changes,
            context.user.user_id,
        )
    except ValueError as exc:
        raise agent_management_http_error(exc) from exc
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    reset_sessions = []
    after_provider_state = _team_member_model_provider_state(agent)
    provider_changed = before_provider_state != after_provider_state
    if request.reset_session and provider_changed:
        reset_sessions = PersistentAgentSessionManagementService(session).reset_team_agent_sessions(
            workspace_id=context.workspace.id,
            team_id=team_id,
            agent_profile_id=agent.id,
            reason="team_member_model_provider_updated",
            metadata={
                "team_member_id": str(member.id),
                "before": before_provider_state,
                "after": after_provider_state,
            },
        )
        session.commit()
        session.refresh(agent)
        after_provider_state = _team_member_model_provider_state(agent)
    AuditService(session).record_user_action(
        workspace_id=context.workspace.id,
        user_id=context.user.user_id,
        action="team.member_model_provider.updated",
        target_type="agent_team_member",
        target_id=member.id,
        metadata={
            "team_id": str(team_id),
            "agent_profile_id": str(agent.id),
            "changed": provider_changed,
            "reset_session": request.reset_session,
            "reset_session_count": len(reset_sessions),
            "before": before_provider_state,
            "after": after_provider_state,
        },
    )
    _append_team_model_provider_updated_message(
        session=session,
        workspace_id=context.workspace.id,
        team_id=team_id,
        team_member_id=member.id,
        agent_profile_id=agent.id,
        actor_user_id=context.user.user_id,
        changed=provider_changed,
        reset_session=request.reset_session,
        reset_session_count=len(reset_sessions),
        before=before_provider_state,
        after=after_provider_state,
    )
    session.commit()
    session.refresh(agent)
    response = agent_profile_response(session, agent)
    if reset_sessions:
        response.model_provider["session_reset"] = {
            "reset_session_count": len(reset_sessions),
            "session_ids": [str(item.id) for item in reset_sessions],
            "reason": "team_member_model_provider_updated",
        }
    return response


def _append_team_model_provider_updated_message(
    *,
    session: Session,
    workspace_id: UUID,
    team_id: UUID,
    team_member_id: UUID,
    agent_profile_id: UUID,
    actor_user_id: UUID,
    changed: bool,
    reset_session: bool,
    reset_session_count: int,
    before: dict[str, object],
    after: dict[str, object],
) -> None:
    thread = TeamRuntimeService(session).ensure_thread(
        workspace_id=workspace_id,
        team_id=team_id,
    )
    if thread is None:
        return
    message = AgentMessage(
        workspace_id=workspace_id,
        thread_id=thread.id,
        agent_team_id=team_id,
        sender_agent_profile_id=agent_profile_id,
        recipient_agent_profile_id=agent_profile_id,
        message_type="team.runtime.model_provider.updated",
        body=(
            "Team member model provider updated"
            if changed
            else "Team member model provider update reviewed without changes"
        ),
        payload={
            "team_id": str(team_id),
            "team_member_id": str(team_member_id),
            "agent_profile_id": str(agent_profile_id),
            "actor_user_id": str(actor_user_id),
            "changed": changed,
            "reset_session": reset_session,
            "reset_session_count": reset_session_count,
            "before": before,
            "after": after,
        },
        status="sent",
    )
    session.add(message)
    session.flush([message])


def _team_member_model_provider_state(agent: AgentProfile) -> dict[str, object]:
    return {
        "model": agent.model,
        "model_provider_credential_id": str(agent.model_provider_credential_id)
        if agent.model_provider_credential_id is not None
        else None,
        "model_api": configured_model_api(agent.model_settings or {}),
    }


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
