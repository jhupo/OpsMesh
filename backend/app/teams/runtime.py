from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agent_messages.models import AgentMessageThread
from backend.app.agent_runtime.sessions import PersistentAgentSession
from backend.app.teams.models import AgentTeam
from backend.app.teams.operating_context import TeamOperatingContextService
from backend.app.teams.runtime_constants import (
    TEAM_RUNTIME_HEARTBEAT_STALE_AFTER_SECONDS,
    TEAM_RUNTIME_PAUSED,
    TEAM_RUNTIME_RUNNING,
    TEAM_RUNTIME_SESSION_SCOPE,
    TEAM_RUNTIME_STALL_STATUSES,
    TEAM_RUNTIME_STALL_THRESHOLD,
    TEAM_RUNTIME_STATUS_KEY,
    TEAM_RUNTIME_STOPPED,
    TEAM_RUNTIME_THREAD_KEY,
    TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY,
)
from backend.app.teams.runtime_heartbeat import TeamRuntimeHeartbeatRecorder
from backend.app.teams.runtime_lifecycle import TeamRuntimeLifecycleService
from backend.app.teams.runtime_mailbox import TeamRuntimeMailboxStore
from backend.app.teams.runtime_refs import team_bound_runtime_id, team_runtime_ref
from backend.app.teams.runtime_repository import TeamRuntimeRepository
from backend.app.teams.runtime_service_binding import TeamRuntimeBindingMixin
from backend.app.teams.runtime_service_heartbeat import TeamRuntimeHeartbeatMixin
from backend.app.teams.runtime_service_lifecycle import TeamRuntimeLifecycleMixin
from backend.app.teams.runtime_sessions import TeamRuntimeSessionStore
from backend.app.teams.runtime_state_builder import (
    TeamRuntimeState,
    TeamRuntimeStateBuilder,
)
from backend.app.teams.runtime_workspace_binding import (
    TeamWorkspaceRuntimeBindingService,
)

__all__ = [
    "TEAM_RUNTIME_HEARTBEAT_STALE_AFTER_SECONDS",
    "TEAM_RUNTIME_PAUSED",
    "TEAM_RUNTIME_RUNNING",
    "TEAM_RUNTIME_SESSION_SCOPE",
    "TEAM_RUNTIME_STALL_STATUSES",
    "TEAM_RUNTIME_STALL_THRESHOLD",
    "TEAM_RUNTIME_STATUS_KEY",
    "TEAM_RUNTIME_STOPPED",
    "TEAM_RUNTIME_THREAD_KEY",
    "TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY",
    "TeamRuntimeService",
    "TeamRuntimeState",
    "team_bound_runtime_id",
    "team_runtime_ref",
]


class TeamRuntimeService(
    TeamRuntimeBindingMixin,
    TeamRuntimeHeartbeatMixin,
    TeamRuntimeLifecycleMixin,
):
    """Persistent controls and shared context for a team/company runtime."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = TeamRuntimeRepository(session)
        self._mailbox = TeamRuntimeMailboxStore(session)
        self._sessions = TeamRuntimeSessionStore(session)
        self._state_builder = TeamRuntimeStateBuilder(
            repo=self._repo,
            mailbox=self._mailbox,
            operating_context=TeamOperatingContextService(session),
        )
        self._heartbeat = TeamRuntimeHeartbeatRecorder(
            session=session,
            repo=self._repo,
            mailbox=self._mailbox,
        )
        self._lifecycle = TeamRuntimeLifecycleService(
            session=session,
            repo=self._repo,
            mailbox=self._mailbox,
            sessions=self._sessions,
            state_builder=self._state_builder,
        )
        self._workspace_binding = TeamWorkspaceRuntimeBindingService(
            session=session,
            repo=self._repo,
            mailbox=self._mailbox,
            sessions=self._sessions,
            state_builder=self._state_builder,
        )

    def get_state(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        initialize: bool = False,
    ) -> TeamRuntimeState | None:
        team = self._repo.team(workspace_id, team_id)
        if team is None:
            return None
        thread: AgentMessageThread | None
        team_session: PersistentAgentSession | None
        if initialize:
            thread = self._mailbox._get_or_create_thread(team)
            team_session = self._sessions._get_or_create_team_session(team)
            member_sessions = self._sessions.member_sessions(team)
            self._session.flush()
        else:
            thread = self._mailbox._existing_thread(team)
            team_session = self._sessions._existing_team_session(team)
            member_sessions = self._sessions._existing_member_sessions(team)
        return self._state(team, team_session, thread, member_sessions)

    def ensure_thread(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
    ) -> AgentMessageThread | None:
        team = self._repo.team(workspace_id, team_id)
        if team is None:
            return None
        thread = self._mailbox._get_or_create_thread(team)
        self._session.flush()
        return thread

    def ensure_member_sessions(self, team: AgentTeam) -> list[PersistentAgentSession]:
        return self._sessions.member_sessions(team)

    def _state(
        self,
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
