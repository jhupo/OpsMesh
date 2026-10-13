from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.agents.messages.models import AgentMessageThread
from opsmesh.agents.sessions.models import AgentSessionMetadata
from opsmesh.governance.audit.service import AuditService
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.instances.service import RuntimeEnvironmentService
from opsmesh.teams.management.models import (
    TEAM_RUNTIME_RUNNING,
    TEAM_RUNTIME_STATUS_KEY,
    TEAM_RUNTIME_STOPPED,
    TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY,
    AgentTeam,
)
from opsmesh.teams.sessions.agent_sessions import TeamAgentSessionStore
from opsmesh.teams.sessions.mailbox import TeamMailboxStore
from opsmesh.teams.sessions.refs import team_runtime_metadata
from opsmesh.teams.sessions.repository import TeamOperationRepository
from opsmesh.teams.sessions.state_builder import TeamOperationSnapshot, TeamOperationSnapshotBuilder


class TeamWorkspaceRuntimeBindingService:
    """Bind and ensure workspace runtimes for a team runtime."""

    def __init__(
        self,
        *,
        session: Session,
        repo: TeamOperationRepository,
        mailbox: TeamMailboxStore,
        sessions: TeamAgentSessionStore,
        state_builder: TeamOperationSnapshotBuilder,
    ) -> None:
        self._session = session
        self._repo = repo
        self._mailbox = mailbox
        self._sessions = sessions
        self._state_builder = state_builder

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
        team = self._repo.team(workspace_id, team_id)
        if team is None:
            return None
        runtime = self._repo.runtime(workspace_id, workspace_runtime_id)
        if runtime is None:
            raise ValueError("Workspace runtime not found")
        if team.runtime_space_id is not None and runtime.runtime_space_id != team.runtime_space_id:
            raise ValueError("Workspace runtime is not in the team's runtime space")

        runtime_metadata = bind_runtime_metadata(
            team=team,
            runtime=runtime,
            actor_user_id=actor_user_id,
            reason=reason,
            metadata=metadata,
        )
        return self._persist_binding(
            team=team,
            actor_user_id=actor_user_id,
            action="team.runtime.bound",
            message_type="team.runtime.bound",
            body=reason or "Workspace runtime bound to team runtime",
            runtime_metadata=runtime_metadata,
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
        team = self._repo.team(workspace_id, team_id)
        if team is None:
            return None

        runtime, resolved_template_id, created = self._resolve_or_create_runtime(
            team=team,
            runtime_control=runtime_control,
            template_id=template_id,
            name=name,
        )
        runtime, started = self._start_runtime_if_needed(
            team=team,
            runtime=runtime,
            runtime_control=runtime_control,
            start=start,
        )
        runtime_metadata = ensure_runtime_metadata(
            team=team,
            runtime=runtime,
            resolved_template_id=resolved_template_id,
            created=created,
            started=started,
            actor_user_id=actor_user_id,
            start=start,
            reason=reason,
            metadata=metadata,
        )
        return self._persist_binding(
            team=team,
            actor_user_id=actor_user_id,
            action="team.runtime.ensured",
            message_type="team.runtime.ensured",
            body=reason or "Workspace runtime ensured for team runtime",
            runtime_metadata=runtime_metadata,
        )

    def _resolve_or_create_runtime(
        self,
        *,
        team: AgentTeam,
        runtime_control: RuntimeEnvironmentService,
        template_id: UUID | None,
        name: str | None,
    ) -> tuple[WorkspaceRuntime, UUID | None, bool]:
        runtime = self._repo.bound_runtime(team)
        if runtime is not None:
            return runtime, template_id, False

        resolved_template_id = self._repo.resolve_template_id(team, template_id)
        if resolved_template_id is None:
            raise ValueError("No active runtime template available")
        runtime = runtime_control.create_runtime(
            workspace_id=team.workspace_id,
            template_id=resolved_template_id,
            name=name or f"{team.name} team runtime",
            runtime_space_id=team.runtime_space_id,
        )
        if runtime is None:
            raise ValueError("Runtime template not found")
        return runtime, resolved_template_id, True

    def _start_runtime_if_needed(
        self,
        *,
        team: AgentTeam,
        runtime: WorkspaceRuntime,
        runtime_control: RuntimeEnvironmentService,
        start: bool,
    ) -> tuple[WorkspaceRuntime, bool]:
        if not start:
            return runtime, False
        if runtime.status == "active":
            return runtime, False
        started_runtime = runtime_control.set_status(team.workspace_id, runtime.id, "active")
        if started_runtime is None:
            raise ValueError("Workspace runtime not found")
        return started_runtime, True

    def _persist_binding(
        self,
        *,
        team: AgentTeam,
        actor_user_id: UUID,
        action: str,
        message_type: str,
        body: str,
        runtime_metadata: dict[str, object],
    ) -> TeamOperationSnapshot:
        thread = self._mailbox._get_or_create_thread(team)
        team_session = self._sessions._get_or_create_team_session(team)
        member_sessions = self._sessions.ensure_member_sessions(team)
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


def bind_runtime_metadata(
    *,
    team: AgentTeam,
    runtime: WorkspaceRuntime,
    actor_user_id: UUID,
    reason: str | None,
    metadata: dict[str, object] | None,
) -> dict[str, object]:
    policy = dict(team.default_task_policy or {})
    runtime_metadata = team_runtime_metadata(team)
    runtime_metadata[TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY] = str(runtime.id)
    runtime_metadata["runtime_bound_at"] = datetime.now(UTC).isoformat()
    runtime_metadata["sandbox_backend_id"] = runtime.sandbox_backend_id
    runtime_metadata["updated_by_user_id"] = str(actor_user_id)
    _optional_metadata(runtime_metadata, reason=reason, metadata=metadata)
    policy[TEAM_RUNTIME_STATUS_KEY] = runtime_metadata
    team.default_task_policy = policy
    return runtime_metadata


def ensure_runtime_metadata(
    *,
    team: AgentTeam,
    runtime: WorkspaceRuntime,
    resolved_template_id: UUID | None,
    created: bool,
    started: bool,
    actor_user_id: UUID,
    start: bool,
    reason: str | None,
    metadata: dict[str, object] | None,
) -> dict[str, object]:
    policy = dict(team.default_task_policy or {})
    runtime_metadata = team_runtime_metadata(team)
    ensured_at = datetime.now(UTC).isoformat()
    runtime_metadata.update(
        {
            "status": TEAM_RUNTIME_RUNNING
            if start
            else runtime_metadata.get("status", TEAM_RUNTIME_STOPPED),
            TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY: str(runtime.id),
            "runtime_bound_at": runtime_metadata.get("runtime_bound_at") or ensured_at,
            "runtime_ensured_at": ensured_at,
            "runtime_created": created,
            "runtime_started": started,
            "updated_by_user_id": str(actor_user_id),
        }
    )
    if resolved_template_id is not None:
        runtime_metadata["runtime_template_id"] = str(resolved_template_id)
    _optional_metadata(runtime_metadata, reason=reason, metadata=metadata)
    policy[TEAM_RUNTIME_STATUS_KEY] = runtime_metadata
    team.default_task_policy = policy
    return runtime_metadata


def _optional_metadata(
    runtime_metadata: dict[str, object],
    *,
    reason: str | None,
    metadata: dict[str, object] | None,
) -> None:
    if reason:
        runtime_metadata["reason"] = reason
    if metadata:
        runtime_metadata["metadata"] = dict(metadata)
