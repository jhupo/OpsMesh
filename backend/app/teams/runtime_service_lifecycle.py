from __future__ import annotations

from uuid import UUID

from backend.app.runtime_manager.lifecycle.control import RuntimeLifecycleControl
from backend.app.teams.runtime_constants import (
    TEAM_RUNTIME_PAUSED,
    TEAM_RUNTIME_RUNNING,
    TEAM_RUNTIME_STOPPED,
)
from backend.app.teams.runtime_lifecycle import TeamRuntimeLifecycleService
from backend.app.teams.runtime_state_builder import TeamRuntimeState


class TeamRuntimeLifecycleMixin:
    _lifecycle: TeamRuntimeLifecycleService

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
