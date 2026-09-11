from __future__ import annotations

from uuid import UUID

from backend.app.runtime_manager.contracts import RuntimeLimits
from backend.app.runtime_manager.lifecycle_control import RuntimeLifecycleControl
from backend.app.teams.runtime_state_builder import TeamRuntimeState
from backend.app.teams.runtime_workspace_binding import TeamWorkspaceRuntimeBindingService


class TeamRuntimeBindingMixin:
    _workspace_binding: TeamWorkspaceRuntimeBindingService

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
