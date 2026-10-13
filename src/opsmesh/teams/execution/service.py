from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.orchestration.runs.service import RunOrchestrationService
from opsmesh.runtime.instances.service import RuntimeEnvironmentService
from opsmesh.teams.execution.finalization import TeamExecutionFinalizationService
from opsmesh.teams.execution.persistence import (
    TeamOrchestrationRecorder,
    TeamOrchestrationRepository,
)
from opsmesh.teams.execution.state import (
    TeamOrchestrationStatusService,
    _advanced,
    _advancement_summary,
)
from opsmesh.teams.operations.command_center import TeamCommandCenterService
from opsmesh.teams.sessions.service import (
    TEAM_RUNTIME_PAUSED,
    TEAM_RUNTIME_RUNNING,
    TEAM_RUNTIME_STOPPED,
    TeamOperationService,
)


class TeamOrchestrationService:
    """Advance team tasks when execution, handoff, and PM acceptance are complete."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = TeamOrchestrationRepository(session)
        self._recorder = TeamOrchestrationRecorder(session)

    def get_status(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        include_completed: bool = False,
        queue_limit: int = 50,
        max_finalize_tasks: int = 50,
    ) -> dict[str, object] | None:
        return TeamOrchestrationStatusService(self._session).get_status(
            workspace_id=workspace_id,
            team_id=team_id,
            include_completed=include_completed,
            queue_limit=queue_limit,
            max_finalize_tasks=max_finalize_tasks,
        )

    def advance(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        dry_run: bool = True,
        apply_command_center_actions: bool = True,
        enqueue_runs: bool = True,
        finalize_ready_tasks: bool = True,
        include_completed: bool = False,
        queue_limit: int = 50,
        sources: list[str] | None = None,
        actions: list[str] | None = None,
        max_actions: int = 5,
        max_tasks_per_action: int = 100,
        max_finalize_tasks: int = 50,
        runtime_control: RuntimeEnvironmentService | None = None,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        team = self._repo.team(workspace_id=workspace_id, team_id=team_id)
        if team is None:
            return None
        runtime_metadata = team.default_task_policy.get("team_runtime", {})
        runtime_status = (
            runtime_metadata.get("status") if isinstance(runtime_metadata, dict) else None
        )
        if runtime_status in {TEAM_RUNTIME_PAUSED, TEAM_RUNTIME_STOPPED}:
            summary: dict[str, object] = {
                "runtime_status": runtime_status,
                "reason": "team_runtime_not_running",
            }
            response = {
                "workspace_id": workspace_id,
                "team_id": team_id,
                "generated_at": datetime.now(UTC),
                "dry_run": dry_run,
                "status": "skipped",
                "summary": summary,
                "command_center_actions": None,
                "finalization": None,
            }
            if not dry_run:
                self._record_advancement(
                    workspace_id=workspace_id,
                    team_id=team_id,
                    actor_user_id=actor_user_id,
                    status="skipped",
                    summary=summary,
                )
            return response
        if not dry_run:
            runtime_service = TeamOperationService(self._session)
            if runtime_status != TEAM_RUNTIME_RUNNING:
                runtime_service.start(
                    workspace_id=workspace_id,
                    team_id=team_id,
                    actor_user_id=actor_user_id,
                    reason="orchestration_started_runtime",
                    metadata=metadata,
                )
            else:
                runtime_service.get_or_initialize_state(
                    workspace_id=workspace_id,
                    team_id=team_id,
                    initialize=True,
                )
        finalization = None
        if finalize_ready_tasks:
            finalization = self.finalize_ready_tasks(
                workspace_id=workspace_id,
                team_id=team_id,
                actor_user_id=actor_user_id,
                dry_run=dry_run,
                max_tasks=max_finalize_tasks,
            )
            if finalization is None:
                return None

        command_center_actions = None
        if apply_command_center_actions:
            command_center_actions = TeamCommandCenterService(self._session).apply_action_plan(
                workspace_id=workspace_id,
                team_id=team_id,
                actor_user_id=actor_user_id,
                include_completed=include_completed,
                queue_limit=queue_limit,
                dry_run=dry_run,
                sources=sources,
                actions=actions,
                max_actions=max_actions,
                max_tasks_per_action=max_tasks_per_action,
                enqueue_runs=enqueue_runs,
                runtime_control=runtime_control,
                reason=reason,
                metadata=metadata,
            )
            if command_center_actions is None:
                return None

        summary = _advancement_summary(
            command_center_actions=command_center_actions,
            finalization=finalization,
            apply_command_center_actions=apply_command_center_actions,
            finalize_ready_tasks=finalize_ready_tasks,
        )
        if not dry_run:
            self._record_advancement(
                workspace_id=workspace_id,
                team_id=team_id,
                actor_user_id=actor_user_id,
                status="advanced" if _advanced(summary) else "noop",
                summary=summary,
            )

        return {
            "workspace_id": workspace_id,
            "team_id": team_id,
            "generated_at": datetime.now(UTC),
            "dry_run": dry_run,
            "status": "dry_run" if dry_run else "advanced" if _advanced(summary) else "noop",
            "summary": summary,
            "command_center_actions": command_center_actions,
            "finalization": finalization,
        }

    def advance_task(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        task_id: UUID,
        actor_user_id: UUID,
    ) -> dict[str, object]:
        """A task event may advance only its own persisted plan and acceptance."""
        team = self._repo.team(workspace_id=workspace_id, team_id=team_id)
        state = team.default_task_policy.get("team_runtime", {}) if team is not None else {}
        if not isinstance(state, dict) or state.get("status") != TEAM_RUNTIME_RUNNING:
            return {"status": "skipped", "reason": "team_runtime_not_running"}
        runs = RunOrchestrationService(self._session).schedule_team_steps(
            workspace_id=workspace_id,
            team_id=team_id,
            task_id=task_id,
            requested_by_user_id=actor_user_id,
        )
        finalization = TeamExecutionFinalizationService(self._session).finalize_ready_tasks(
            workspace_id=workspace_id,
            team_id=team_id,
            task_id=task_id,
            actor_user_id=actor_user_id,
            dry_run=False,
            max_tasks=1,
        )
        return {
            "task_id": str(task_id),
            "scheduled_run_ids": [str(run.id) for run in runs],
            "finalization": finalization,
        }

    def finalize_ready_tasks(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID | None,
        dry_run: bool = True,
        max_tasks: int = 50,
    ) -> dict[str, object] | None:
        return TeamExecutionFinalizationService(self._session).finalize_ready_tasks(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=actor_user_id,
            dry_run=dry_run,
            max_tasks=max_tasks,
        )

    def _record_advancement(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        status: str,
        summary: dict[str, object],
    ) -> None:
        self._recorder.record_advancement(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=actor_user_id,
            status=status,
            summary=summary,
        )
