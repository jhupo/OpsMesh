from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.agents.messages.models import AgentMessageThread
from opsmesh.agents.sessions.models import AgentSessionMetadata
from opsmesh.runtime.instances.service import RuntimeEnvironmentService
from opsmesh.teams.management.models import (
    TEAM_RUNTIME_PAUSED,
    TEAM_RUNTIME_RUNNING,
    TEAM_RUNTIME_SESSION_SCOPE,
    TEAM_RUNTIME_STATUS_KEY,
    TEAM_RUNTIME_STOPPED,
    TEAM_RUNTIME_THREAD_KEY,
    TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY,
    AgentTeam,
)
from opsmesh.teams.organization.service import TeamOperatingContextService
from opsmesh.teams.sessions.advancement import TeamAdvancementRecorder
from opsmesh.teams.sessions.agent_sessions import TeamAgentSessionStore
from opsmesh.teams.sessions.binding import TeamWorkspaceRuntimeBindingService
from opsmesh.teams.sessions.lifecycle import TeamOperationLifecycleService
from opsmesh.teams.sessions.mailbox import TeamMailboxStore
from opsmesh.teams.sessions.refs import team_bound_runtime_id, team_runtime_ref
from opsmesh.teams.sessions.repository import TeamOperationRepository
from opsmesh.teams.sessions.state_builder import TeamOperationSnapshot, TeamOperationSnapshotBuilder

__all__ = [
    "TEAM_RUNTIME_PAUSED",
    "TEAM_RUNTIME_RUNNING",
    "TEAM_RUNTIME_SESSION_SCOPE",
    "TEAM_RUNTIME_STATUS_KEY",
    "TEAM_RUNTIME_STOPPED",
    "TEAM_RUNTIME_THREAD_KEY",
    "TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY",
    "TeamOperationService",
    "TeamOperationSnapshot",
    "team_bound_runtime_id",
    "team_runtime_ref",
]


class TeamOperationService:
    """Persistent controls and shared context for a team/company runtime."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = TeamOperationRepository(session)
        self._mailbox = TeamMailboxStore(session)
        self._sessions = TeamAgentSessionStore(session)
        self._state_builder = TeamOperationSnapshotBuilder(
            repo=self._repo,
            mailbox=self._mailbox,
            operating_context=TeamOperatingContextService(session),
        )
        self._advancement = TeamAdvancementRecorder(
            session=session,
            repo=self._repo,
            mailbox=self._mailbox,
        )
        self._lifecycle = TeamOperationLifecycleService(
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

    def get_or_initialize_state(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        initialize: bool = False,
    ) -> TeamOperationSnapshot | None:
        """Return team state, optionally creating its shared execution context.

        Args:
            workspace_id: Tenant scope of the team lookup.
            team_id: Team whose state is requested.
            initialize: Create missing thread and team/member sessions when true.
                The default reads existing context without creating it.

        Returns:
            The current snapshot, or None if the scoped team does not exist.

        Initialization flushes the borrowed Session; the caller owns commit and
        rollback. It does not start the Runtime or execute an Agent.
        """
        team = self._repo.team(workspace_id, team_id)
        if team is None:
            return None
        thread: AgentMessageThread | None
        team_session: AgentSessionMetadata | None
        if initialize:
            thread = self._mailbox._get_or_create_thread(team)
            team_session = self._sessions._get_or_create_team_session(team)
            member_sessions = self._sessions.ensure_member_sessions(team)
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

    def bind_runtime(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        workspace_runtime_id: UUID,
        actor_user_id: UUID,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TeamOperationSnapshot | None:
        return self._workspace_binding.bind_runtime(
            workspace_id=workspace_id,
            team_id=team_id,
            workspace_runtime_id=workspace_runtime_id,
            actor_user_id=actor_user_id,
            reason=reason,
            metadata=metadata,
        )

    def ensure_workspace_runtime(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        runtime_control: RuntimeEnvironmentService,
        template_id: UUID | None = None,
        name: str | None = None,
        start: bool = True,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TeamOperationSnapshot | None:
        return self._workspace_binding.ensure_workspace_runtime(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=actor_user_id,
            runtime_control=runtime_control,
            template_id=template_id,
            name=name,
            start=start,
            reason=reason,
            metadata=metadata,
        )

    def record_advancement(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        status: str,
        summary: dict[str, object],
    ) -> None:
        self._advancement.record_advancement(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=actor_user_id,
            status=status,
            summary=summary,
        )

    def start(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        runtime_control: RuntimeEnvironmentService | None = None,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TeamOperationSnapshot | None:
        return self._transition(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=actor_user_id,
            status=TEAM_RUNTIME_RUNNING,
            event_type="team.runtime.started",
            runtime_control=runtime_control,
            reason=reason,
            metadata=metadata,
        )

    def pause(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        runtime_control: RuntimeEnvironmentService | None = None,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TeamOperationSnapshot | None:
        return self._transition(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=actor_user_id,
            status=TEAM_RUNTIME_PAUSED,
            event_type="team.runtime.paused",
            runtime_control=runtime_control,
            reason=reason,
            metadata=metadata,
        )

    def resume(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        runtime_control: RuntimeEnvironmentService | None = None,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TeamOperationSnapshot | None:
        return self._transition(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=actor_user_id,
            status=TEAM_RUNTIME_RUNNING,
            event_type="team.runtime.resumed",
            runtime_control=runtime_control,
            reason=reason,
            metadata=metadata,
        )

    def stop(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        runtime_control: RuntimeEnvironmentService | None = None,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TeamOperationSnapshot | None:
        return self._transition(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=actor_user_id,
            status=TEAM_RUNTIME_STOPPED,
            event_type="team.runtime.stopped",
            runtime_control=runtime_control,
            reason=reason,
            metadata=metadata,
        )

    def continue_runtime(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        runtime_control: RuntimeEnvironmentService | None = None,
        instruction: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TeamOperationSnapshot | None:
        return self._transition(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=actor_user_id,
            status=TEAM_RUNTIME_RUNNING,
            event_type="team.runtime.continued",
            runtime_control=runtime_control,
            reason=instruction,
            metadata=metadata,
        )

    def _transition(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        status: str,
        event_type: str,
        runtime_control: RuntimeEnvironmentService | None,
        reason: str | None,
        metadata: dict[str, object] | None,
    ) -> TeamOperationSnapshot | None:
        return self._lifecycle.transition(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=actor_user_id,
            status=status,
            event_type=event_type,
            runtime_control=runtime_control,
            reason=reason,
            metadata=metadata,
        )

    def _state(
        self,
        team: AgentTeam,
        team_session: AgentSessionMetadata | None,
        thread: AgentMessageThread | None,
        member_sessions: list[AgentSessionMetadata],
    ) -> TeamOperationSnapshot:
        return self._state_builder.build(
            team=team,
            team_session=team_session,
            thread=thread,
            member_sessions=member_sessions,
        )
