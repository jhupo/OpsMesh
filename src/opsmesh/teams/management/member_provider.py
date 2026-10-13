from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.agents.messages.models import AgentMessage
from opsmesh.agents.profiles.contracts import AgentProfileResponse
from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.agents.profiles.service import AgentProfileService
from opsmesh.agents.providers.model_api import configured_model_api, require_known_model_api
from opsmesh.agents.providers.views import agent_profile_response
from opsmesh.agents.sessions.service import AgentSessionService
from opsmesh.governance.audit.service import AuditService
from opsmesh.teams.management.models import AgentTeamMember
from opsmesh.teams.operations.schemas import AgentTeamMemberModelProviderUpdateRequest
from opsmesh.teams.sessions.service import TeamOperationService


def update_team_member_model_provider(
    session: Session,
    *,
    workspace_id: UUID,
    team_id: UUID,
    member_id: UUID,
    request: AgentTeamMemberModelProviderUpdateRequest,
    actor_user_id: UUID,
) -> AgentProfileResponse:
    member = session.scalar(
        select(AgentTeamMember)
        .where(
            AgentTeamMember.workspace_id == workspace_id,
            AgentTeamMember.agent_team_id == team_id,
            AgentTeamMember.id == member_id,
        )
        .with_for_update()
    )
    if member is None:
        raise ValueError("Team member not found")
    before_agent = session.scalar(
        select(AgentProfile)
        .where(
            AgentProfile.workspace_id == workspace_id,
            AgentProfile.id == member.agent_profile_id,
        )
        .with_for_update()
    )
    if before_agent is None:
        raise ValueError("Agent not found")
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
        raise ValueError("Provide model_provider_credential_id, model, or model_api to update")
    try:
        agent = AgentProfileService(session).update_agent(
            workspace_id,
            member.agent_profile_id,
            changes,
            actor_user_id,
            commit=False,
        )
    except BaseException:
        session.rollback()
        raise
    if agent is None:
        raise ValueError("Agent not found")
    try:
        reset_sessions = []
        after_provider_state = _team_member_model_provider_state(agent)
        provider_changed = before_provider_state != after_provider_state
        if request.reset_session and provider_changed:
            reset_sessions = AgentSessionService(session).reset_team_agent_sessions(
                workspace_id=workspace_id,
                team_id=team_id,
                agent_profile_id=agent.id,
                reason="team_member_model_provider_updated",
                metadata={
                    "team_member_id": str(member.id),
                    "before": before_provider_state,
                    "after": after_provider_state,
                },
            )
        AuditService(session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
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
            workspace_id=workspace_id,
            team_id=team_id,
            team_member_id=member.id,
            agent_profile_id=agent.id,
            actor_user_id=actor_user_id,
            changed=provider_changed,
            reset_session=request.reset_session,
            reset_session_count=len(reset_sessions),
            before=before_provider_state,
            after=after_provider_state,
        )
        session.commit()
        session.refresh(agent)
    except BaseException:
        session.rollback()
        raise
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
    thread = TeamOperationService(session).ensure_thread(
        workspace_id=workspace_id,
        team_id=team_id,
    )
    if thread is None:
        raise ValueError("Team not found")
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
