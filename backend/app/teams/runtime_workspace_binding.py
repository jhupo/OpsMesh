from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.runtime_manager.contracts import RuntimeLimits
from backend.app.runtime_manager.lifecycle.control import RuntimeLifecycleControl
from backend.app.runtime_manager.models import WorkspaceRuntime
from backend.app.teams.models import AgentTeam
from backend.app.teams.runtime_mailbox import TeamRuntimeMailboxStore
from backend.app.teams.runtime_repository import TeamRuntimeRepository
from backend.app.teams.runtime_sessions import TeamRuntimeSessionStore
from backend.app.teams.runtime_state_builder import TeamRuntimeState, TeamRuntimeStateBuilder
from backend.app.teams.runtime_workspace_binding_commit import (
    TeamWorkspaceRuntimeBindingCommitter,
)
from backend.app.teams.runtime_workspace_binding_payloads import (
    attach_team_runtime_capabilities,
    bind_runtime_metadata,
    ensure_runtime_metadata,
)


class TeamWorkspaceRuntimeBindingService:
    """Bind and ensure workspace runtimes for a team runtime."""

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
        self._committer = TeamWorkspaceRuntimeBindingCommitter(
            session=session,
            mailbox=mailbox,
            sessions=sessions,
            state_builder=state_builder,
        )

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
        attach_team_runtime_capabilities(
            team=team,
            runtime=runtime,
            bound_at=str(runtime_metadata["runtime_bound_at"]),
        )
        return self._committer.commit(
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
        runtime_control: RuntimeLifecycleControl,
        template_id: UUID | None = None,
        name: str | None = None,
        limits: RuntimeLimits | None = None,
        network_disabled: bool = True,
        start: bool = True,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TeamRuntimeState | None:
        team = self._repo.team(workspace_id, team_id)
        if team is None:
            return None

        runtime, resolved_template_id, created = self._resolve_or_create_runtime(
            team=team,
            runtime_control=runtime_control,
            template_id=template_id,
            name=name,
            limits=limits,
            network_disabled=network_disabled,
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
        attach_team_runtime_capabilities(
            team=team,
            runtime=runtime,
            bound_at=str(runtime_metadata["runtime_bound_at"]),
            ensured_at=str(runtime_metadata["runtime_ensured_at"]),
        )
        return self._committer.commit(
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
        runtime_control: RuntimeLifecycleControl,
        template_id: UUID | None,
        name: str | None,
        limits: RuntimeLimits | None,
        network_disabled: bool,
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
            limits=limits,
            runtime_space_id=team.runtime_space_id,
            network_disabled=network_disabled,
        )
        if runtime is None:
            raise ValueError("Runtime template not found")
        return runtime, resolved_template_id, True

    def _start_runtime_if_needed(
        self,
        *,
        team: AgentTeam,
        runtime: WorkspaceRuntime,
        runtime_control: RuntimeLifecycleControl,
        start: bool,
    ) -> tuple[WorkspaceRuntime, bool]:
        if not start:
            return runtime, False
        if runtime.status == "running" and runtime.connection_status not in {"offline", "error"}:
            return runtime, False
        started_runtime = runtime_control.start_runtime(team.workspace_id, runtime.id)
        if started_runtime is None:
            raise ValueError("Workspace runtime not found")
        return started_runtime, True
