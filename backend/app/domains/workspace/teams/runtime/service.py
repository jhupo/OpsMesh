from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.domains.agents.messages.models import AgentMessageThread
from backend.app.domains.agents.sessions.models import PersistentAgentSession
from backend.app.domains.workspace.teams.models import (
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
    AgentTeam,
)
from backend.app.domains.workspace.teams.organization.service import (
    TeamOperatingContextService,
)
from backend.app.domains.workspace.teams.runtime.binding import (
    TeamWorkspaceRuntimeBindingService,
)
from backend.app.domains.workspace.teams.runtime.heartbeat import TeamRuntimeHeartbeatRecorder
from backend.app.domains.workspace.teams.runtime.lifecycle import TeamRuntimeLifecycleService
from backend.app.domains.workspace.teams.runtime.mailbox import TeamRuntimeMailboxStore
from backend.app.domains.workspace.teams.runtime.refs import team_bound_runtime_id, team_runtime_ref
from backend.app.domains.workspace.teams.runtime.repository import TeamRuntimeRepository
from backend.app.domains.workspace.teams.runtime.sessions import TeamRuntimeSessionStore
from backend.app.domains.workspace.teams.runtime.state_builder import (
    TeamRuntimeState,
    TeamRuntimeStateBuilder,
)
from backend.app.runtime.environment.contracts import RuntimeLimits
from backend.app.runtime.environment.lifecycle.control import RuntimeLifecycleControl

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


class TeamRuntimeService:
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

    def bind_runtime(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        workspace_runtime_id: UUID,
        actor_user_id: UUID,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TeamRuntimeState | None:
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
        runtime_control: RuntimeLifecycleControl,
        template_id: UUID | None = None,
        name: str | None = None,
        limits: RuntimeLimits | None = None,
        network_disabled: bool = True,
        start: bool = True,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TeamRuntimeState | None:
        return self._workspace_binding.ensure_workspace_runtime(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=actor_user_id,
            runtime_control=runtime_control,
            template_id=template_id,
            name=name,
            limits=limits,
            network_disabled=network_disabled,
            start=start,
            reason=reason,
            metadata=metadata,
        )

    def record_iteration(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        status: str,
        summary: dict[str, object],
    ) -> None:
        self._heartbeat.record_iteration(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=actor_user_id,
            status=status,
            summary=summary,
        )

    def record_worker_failure(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        worker_id: str,
        queue_name: str,
        job_id: UUID,
        status: str,
        attempt: int,
        max_attempts: int,
        will_retry: bool,
        error: BaseException,
        actor_user_id: UUID | None = None,
        routing: dict[str, object] | None = None,
        trace_metadata: dict[str, object] | None = None,
    ) -> None:
        self._heartbeat.record_worker_failure(
            workspace_id=workspace_id,
            team_id=team_id,
            worker_id=worker_id,
            queue_name=queue_name,
            job_id=job_id,
            status=status,
            attempt=attempt,
            max_attempts=max_attempts,
            will_retry=will_retry,
            error=error,
            actor_user_id=actor_user_id,
            routing=routing,
            trace_metadata=trace_metadata,
        )

    def start(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        runtime_control: RuntimeLifecycleControl | None = None,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TeamRuntimeState | None:
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
        runtime_control: RuntimeLifecycleControl | None = None,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TeamRuntimeState | None:
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
        runtime_control: RuntimeLifecycleControl | None = None,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TeamRuntimeState | None:
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
        runtime_control: RuntimeLifecycleControl | None = None,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TeamRuntimeState | None:
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
        runtime_control: RuntimeLifecycleControl | None = None,
        instruction: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TeamRuntimeState | None:
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
        runtime_control: RuntimeLifecycleControl | None,
        reason: str | None,
        metadata: dict[str, object] | None,
    ) -> TeamRuntimeState | None:
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
