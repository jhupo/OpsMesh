from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agent_messages.models import AgentMessageThread
from backend.app.agent_runtime.sessions import PersistentAgentSession
from backend.app.audit.service import AuditService
from backend.app.runtime_manager.lifecycle.control import RuntimeLifecycleControl
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.teams.models import AgentTeam
from backend.app.teams.runtime_constants import (
    TEAM_RUNTIME_PAUSED,
    TEAM_RUNTIME_RUNNING,
    TEAM_RUNTIME_STATUS_KEY,
    TEAM_RUNTIME_STOPPED,
    TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY,
)
from backend.app.teams.runtime_mailbox import TeamRuntimeMailboxStore
from backend.app.teams.runtime_refs import _dict_or_none, team_runtime_metadata
from backend.app.teams.runtime_repository import TeamRuntimeRepository
from backend.app.teams.runtime_sessions import TeamRuntimeSessionStore
from backend.app.teams.runtime_state_builder import TeamRuntimeState, TeamRuntimeStateBuilder


class TeamRuntimeLifecycleService:
    """Apply logical and bound workspace-runtime lifecycle transitions."""

    def __init__(
        self,
        *,
        session: Session,
        repo: TeamRuntimeRepository,
        mailbox: TeamRuntimeMailboxStore,
        sessions: TeamRuntimeSessionStore,
        state_builder: TeamRuntimeStateBuilder,
    ) -> None:
        self._session = session
        self._repo = repo
        self._mailbox = mailbox
        self._sessions = sessions
        self._state_builder = state_builder

    def transition(
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
        team = self._repo.team(workspace_id, team_id)
        if team is None:
            return None
        return self.transition_team(
            team=team,
            actor_user_id=actor_user_id,
            status=status,
            event_type=event_type,
            runtime_control=runtime_control,
            reason=reason,
            metadata=metadata,
        )

    def transition_team(
        self,
        *,
        team: AgentTeam,
        actor_user_id: UUID,
        status: str,
        event_type: str,
        runtime_control: RuntimeLifecycleControl | None,
        reason: str | None,
        metadata: dict[str, object] | None,
    ) -> TeamRuntimeState | None:
        runtime_metadata = self._transition_metadata(
            team=team,
            actor_user_id=actor_user_id,
            status=status,
            event_type=event_type,
            runtime_control=runtime_control,
            reason=reason,
            metadata=metadata,
        )
        thread = self._mailbox._get_or_create_thread(team)
        team_session = self._sessions._get_or_create_team_session(team)
        member_sessions = self._sessions.member_sessions(team)
        self._record_transition(
            team=team,
            thread=thread,
            actor_user_id=actor_user_id,
            event_type=event_type,
            reason=reason,
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

    def _transition_metadata(
        self,
        *,
        team: AgentTeam,
        actor_user_id: UUID,
        status: str,
        event_type: str,
        runtime_control: RuntimeLifecycleControl | None,
        reason: str | None,
        metadata: dict[str, object] | None,
    ) -> dict[str, object]:
        policy = dict(team.default_task_policy or {})
        runtime_metadata = team_runtime_metadata(team)
        updated_at = datetime.now(UTC).isoformat()
        workspace_runtime_control = self._apply_bound_runtime_control(
            team=team,
            desired_status=status,
            runtime_control=runtime_control,
            updated_at=updated_at,
        )
        runtime_metadata.update(
            {
                "status": status,
                "updated_at": updated_at,
                "updated_by_user_id": str(actor_user_id),
            }
        )
        self._clear_or_continue_failure_state(
            runtime_metadata=runtime_metadata,
            status=status,
            event_type=event_type,
            updated_at=updated_at,
        )
        if workspace_runtime_control:
            runtime_metadata["workspace_runtime_control"] = workspace_runtime_control
        if event_type == "team.runtime.continued":
            runtime_metadata["continued_at"] = updated_at
            last_iteration = _dict_or_none(runtime_metadata.get("last_iteration"))
            if last_iteration is not None:
                runtime_metadata["continued_from_iteration"] = last_iteration
        if reason:
            runtime_metadata["reason"] = reason
        if metadata:
            runtime_metadata["metadata"] = dict(metadata)
        policy[TEAM_RUNTIME_STATUS_KEY] = runtime_metadata
        team.default_task_policy = policy
        return runtime_metadata

    def _clear_or_continue_failure_state(
        self,
        *,
        runtime_metadata: dict[str, object],
        status: str,
        event_type: str,
        updated_at: str,
    ) -> None:
        had_worker_failure = "last_worker_failure" in runtime_metadata
        if status != TEAM_RUNTIME_RUNNING:
            return
        runtime_metadata.pop("last_worker_failure", None)
        if had_worker_failure and event_type == "team.runtime.continued":
            runtime_metadata["heartbeat_status"] = TEAM_RUNTIME_RUNNING
            runtime_metadata["last_heartbeat_at"] = updated_at

    def _apply_bound_runtime_control(
        self,
        *,
        team: AgentTeam,
        desired_status: str,
        runtime_control: RuntimeLifecycleControl | None,
        updated_at: str,
    ) -> dict[str, object]:
        runtime = self._repo.bound_runtime(team)
        if runtime is None:
            return {}

        control: dict[str, object] = {
            TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY: str(runtime.id),
            "requested_status": desired_status,
            "runtime_status_before": runtime.status,
            "controlled_at": updated_at,
        }
        if desired_status == TEAM_RUNTIME_RUNNING:
            return self._start_bound_runtime(
                team=team,
                runtime_control=runtime_control,
                runtime=runtime,
                control=control,
            )
        if desired_status in {TEAM_RUNTIME_PAUSED, TEAM_RUNTIME_STOPPED}:
            return self._stop_bound_runtime(
                team=team,
                runtime_control=runtime_control,
                runtime=runtime,
                control=control,
            )
        return control

    def _start_bound_runtime(
        self,
        *,
        team: AgentTeam,
        runtime_control: RuntimeLifecycleControl | None,
        runtime: WorkspaceRuntime,
        control: dict[str, object],
    ) -> dict[str, object]:
        control["action"] = "start"
        if runtime.status == "running":
            control.update({"mode": "already_running", "runtime_status_after": runtime.status})
            return control
        if runtime_control is None:
            control.update({"mode": "logical_only", "reason": "runtime_control_unavailable"})
            return control
        started_runtime = runtime_control.start_runtime(team.workspace_id, runtime.id)
        if started_runtime is None:
            raise ValueError("Workspace runtime not found")
        control.update({"mode": "lifecycle", "runtime_status_after": started_runtime.status})
        return control

    def _stop_bound_runtime(
        self,
        *,
        team: AgentTeam,
        runtime_control: RuntimeLifecycleControl | None,
        runtime: WorkspaceRuntime,
        control: dict[str, object],
    ) -> dict[str, object]:
        control["action"] = "stop"
        if runtime.status != "running":
            control.update({"mode": "already_stopped", "runtime_status_after": runtime.status})
            return control
        if runtime_control is None:
            control.update({"mode": "logical_only", "reason": "runtime_control_unavailable"})
            return control
        stopped_runtime = runtime_control.stop_runtime(team.workspace_id, runtime.id)
        if stopped_runtime is None:
            raise ValueError("Workspace runtime not found")
        control.update({"mode": "lifecycle", "runtime_status_after": stopped_runtime.status})
        return control

    def _record_transition(
        self,
        *,
        team: AgentTeam,
        thread: AgentMessageThread,
        actor_user_id: UUID,
        event_type: str,
        reason: str | None,
        runtime_metadata: dict[str, object],
    ) -> None:
        self._mailbox._append_runtime_message(
            team=team,
            thread=thread,
            actor_user_id=actor_user_id,
            message_type=event_type,
            body=reason or event_type,
            payload=runtime_metadata,
        )
        AuditService(self._session).record_user_action(
            workspace_id=team.workspace_id,
            user_id=actor_user_id,
            action=event_type,
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
