from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_messages.models import AgentMessage, AgentMessageThread
from backend.app.agents.models import AgentProfile
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.teams.runtime_constants import TEAM_RUNTIME_THREAD_KEY
from backend.app.teams.runtime_refs import _uuid_or_none


class TeamRuntimeMailboxStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _get_or_create_thread(self, team: AgentTeam) -> AgentMessageThread:
        policy = dict(team.default_task_policy or {})
        thread_id = _uuid_or_none(policy.get(TEAM_RUNTIME_THREAD_KEY))
        if thread_id is not None:
            thread = self._session.scalar(
                select(AgentMessageThread).where(
                    AgentMessageThread.workspace_id == team.workspace_id,
                    AgentMessageThread.id == thread_id,
                    AgentMessageThread.agent_team_id == team.id,
                )
            )
            if thread is not None:
                return thread
        thread = AgentMessageThread(
            workspace_id=team.workspace_id,
            agent_team_id=team.id,
            subject=f"{team.name} runtime",
            status="active",
        )
        self._session.add(thread)
        self._session.flush([thread])
        policy[TEAM_RUNTIME_THREAD_KEY] = str(thread.id)
        team.default_task_policy = policy
        return thread


    def _existing_thread(self, team: AgentTeam) -> AgentMessageThread | None:
        policy = dict(team.default_task_policy or {})
        thread_id = _uuid_or_none(policy.get(TEAM_RUNTIME_THREAD_KEY))
        if thread_id is None:
            return None
        return self._session.scalar(
            select(AgentMessageThread).where(
                AgentMessageThread.workspace_id == team.workspace_id,
                AgentMessageThread.id == thread_id,
                AgentMessageThread.agent_team_id == team.id,
            )
        )


    def _append_runtime_message(
        self,
        *,
        team: AgentTeam,
        thread: AgentMessageThread,
        actor_user_id: UUID | None,
        message_type: str,
        body: str,
        payload: dict[str, object],
    ) -> AgentMessage:
        sender_agent_id = team.manager_agent_profile_id or self._first_agent_id(team)
        message_payload = {**payload, "team_id": str(team.id)}
        if actor_user_id is not None:
            message_payload["actor_user_id"] = str(actor_user_id)
        message = AgentMessage(
            workspace_id=team.workspace_id,
            thread_id=thread.id,
            agent_team_id=team.id,
            sender_agent_profile_id=sender_agent_id,
            recipient_agent_profile_id=sender_agent_id,
            message_type=message_type,
            body=body,
            payload=message_payload,
            status="sent",
        )
        self._session.add(message)
        self._session.flush([message])
        thread.updated_at = message.created_at
        return message


    def _first_agent_id(self, team: AgentTeam) -> UUID | None:
        return self._session.scalar(
            select(AgentProfile.id)
            .join(AgentTeamMember, AgentTeamMember.agent_profile_id == AgentProfile.id)
            .where(
                AgentTeamMember.workspace_id == team.workspace_id,
                AgentTeamMember.agent_team_id == team.id,
                AgentTeamMember.status == "active",
            )
            .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.id.asc())
            .limit(1)
        )


    def _last_message_at(self, team: AgentTeam, thread: AgentMessageThread) -> datetime | None:
        return self._session.scalar(
            select(AgentMessage.created_at)
            .where(
                AgentMessage.workspace_id == thread.workspace_id,
                AgentMessage.thread_id == thread.id,
                AgentMessage.agent_team_id == team.id,
            )
            .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc())
            .limit(1)
        )


    def _last_iteration_message(
        self,
        team: AgentTeam,
        thread: AgentMessageThread,
    ) -> AgentMessage | None:
        return self._session.scalar(
            select(AgentMessage)
            .where(
                AgentMessage.workspace_id == thread.workspace_id,
                AgentMessage.thread_id == thread.id,
                AgentMessage.agent_team_id == team.id,
                AgentMessage.message_type == "runtime_iteration",
            )
            .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc())
            .limit(1)
        )
