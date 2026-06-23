from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agent_messages.models import AgentMessageThread
from backend.app.agent_runtime.sessions import PersistentAgentSession
from backend.app.audit.service import AuditService
from backend.app.teams.models import AgentTeam
from backend.app.teams.runtime_mailbox import TeamRuntimeMailboxStore
from backend.app.teams.runtime_sessions import TeamRuntimeSessionStore
from backend.app.teams.runtime_state_builder import TeamRuntimeState


class TeamWorkspaceRuntimeBindingCommitter:
    """Persist workspace runtime binding messages, audit, and response state."""

    def __init__(
        self,
        *,
        session: Session,
        mailbox: TeamRuntimeMailboxStore,
        sessions: TeamRuntimeSessionStore,
        state_builder,
    ) -> None:
        self._session = session
        self._mailbox = mailbox
        self._sessions = sessions
        self._state_builder = state_builder

    def commit(
        self,
        *,
        team: AgentTeam,
        actor_user_id: UUID,
        action: str,
        message_type: str,
        body: str,
        runtime_metadata: dict[str, object],
    ) -> TeamRuntimeState:
        thread = self._mailbox._get_or_create_thread(team)
        team_session = self._sessions._get_or_create_team_session(team)
        member_sessions = self._sessions.member_sessions(team)
        self._append_message(
            team=team,
            thread=thread,
            actor_user_id=actor_user_id,
            message_type=message_type,
            body=body,
            runtime_metadata=runtime_metadata,
        )
        self._record_audit(
            team=team,
            actor_user_id=actor_user_id,
            action=action,
            runtime_metadata=runtime_metadata,
        )
        self._session.commit()
        self._session.refresh(team)
        return self._build_state(
            team=team,
            team_session=team_session,
            thread=thread,
            member_sessions=member_sessions,
        )

    def _append_message(
        self,
        *,
        team: AgentTeam,
        thread: AgentMessageThread,
        actor_user_id: UUID,
        message_type: str,
        body: str,
        runtime_metadata: dict[str, object],
    ) -> None:
        self._mailbox._append_runtime_message(
            team=team,
            thread=thread,
            actor_user_id=actor_user_id,
            message_type=message_type,
            body=body,
            payload=runtime_metadata,
        )

    def _record_audit(
        self,
        *,
        team: AgentTeam,
        actor_user_id: UUID,
        action: str,
        runtime_metadata: dict[str, object],
    ) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=team.workspace_id,
            user_id=actor_user_id,
            action=action,
            target_type="agent_team",
            target_id=team.id,
            metadata=runtime_metadata,
        )

    def _build_state(
        self,
        *,
        team: AgentTeam,
        team_session: PersistentAgentSession | None,
        thread: AgentMessageThread | None,
        member_sessions: list[PersistentAgentSession],
    ) -> TeamRuntimeState:
        return self._state_builder.build(
            team=team,
            team_session=team_session,
            thread=thread,
            member_sessions=member_sessions,
        )
