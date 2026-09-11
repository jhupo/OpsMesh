from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.runtime_manager.lifecycle.control import RuntimeLifecycleControl
from backend.app.runtime_manager.quotas import RuntimeQuotaExceededError
from backend.app.runtime_manager.safety import RuntimeSafetyError
from backend.app.teams.command_center import TeamCommandCenterService
from backend.app.teams.execution_loop_finalization import TeamExecutionFinalizationService
from backend.app.teams.execution_loop_jobs import enqueue_team_execution_loop_job
from backend.app.teams.execution_loop_payloads import (
    _advanced,
    _iteration_summary,
)
from backend.app.teams.execution_loop_queue import (
    TeamExecutionLoopEnqueueSummary,
    TeamExecutionLoopQueueService,
)
from backend.app.teams.execution_loop_recorder import TeamExecutionLoopIterationRecorder
from backend.app.teams.execution_loop_repository import TeamExecutionLoopRepository
from backend.app.teams.execution_loop_runtime_candidates import (
    _runtime_status,
)
from backend.app.teams.execution_loop_status import TeamExecutionLoopStatusService
from backend.app.teams.runtime import (
    TEAM_RUNTIME_PAUSED,
    TEAM_RUNTIME_RUNNING,
    TEAM_RUNTIME_STOPPED,
    TeamRuntimeService,
)
from backend.app.workers.queue.redis_queue import RedisQueue

__all__ = [
    "TeamExecutionLoopEnqueueSummary",
    "TeamExecutionLoopQueueService",
    "TeamExecutionLoopService",
    "enqueue_team_execution_loop_job",
]


class TeamExecutionLoopService:
    """Advance team tasks when execution, handoff, and PM acceptance are complete."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = TeamExecutionLoopRepository(session)
        self._recorder = TeamExecutionLoopIterationRecorder(session)

    def get_status(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        include_completed: bool = False,
        queue_limit: int = 50,
        max_finalize_tasks: int = 50,
    ) -> dict[str, object] | None:
        return TeamExecutionLoopStatusService(self._session).get_status(
            workspace_id=workspace_id,
            team_id=team_id,
            include_completed=include_completed,
            queue_limit=queue_limit,
            max_finalize_tasks=max_finalize_tasks,
        )

    def run_iteration(
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
        queue: RedisQueue | None = None,
        runtime_control: RuntimeLifecycleControl | None = None,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        team = self._repo.team(workspace_id=workspace_id, team_id=team_id)
        if team is None:
            return None
        runtime_status = _runtime_status(team)
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
                self._record_iteration(
                    workspace_id=workspace_id,
                    team_id=team_id,
                    actor_user_id=actor_user_id,
                    status="skipped",
                    summary=summary,
                )
            return response
        if not dry_run:
            runtime_service = TeamRuntimeService(self._session)
            if runtime_status != TEAM_RUNTIME_RUNNING:
                runtime_service.start(
                    workspace_id=workspace_id,
                    team_id=team_id,
                    actor_user_id=actor_user_id,
                    reason="execution_loop_started_runtime",
                    metadata=metadata,
                )
            else:
                runtime_service.get_state(
                    workspace_id=workspace_id,
                    team_id=team_id,
                    initialize=True,
                )
            if runtime_control is not None:
                try:
                    runtime_service.ensure_workspace_runtime(
                        workspace_id=workspace_id,
                        team_id=team_id,
                        actor_user_id=actor_user_id,
                        runtime_control=runtime_control,
                        start=True,
                        reason="execution_loop_ensured_runtime",
                        metadata=metadata,
                    )
                except (RuntimeQuotaExceededError, RuntimeSafetyError, ValueError) as exc:
                    summary = {
                        "runtime_status": runtime_status or TEAM_RUNTIME_RUNNING,
                        "reason": "team_workspace_runtime_unavailable",
                        "error": str(exc),
                    }
                    self._record_iteration(
                        workspace_id=workspace_id,
                        team_id=team_id,
                        actor_user_id=actor_user_id,
                        status="skipped",
                        summary=summary,
                    )
                    return {
                        "workspace_id": workspace_id,
                        "team_id": team_id,
                        "generated_at": datetime.now(UTC),
                        "dry_run": dry_run,
                        "status": "skipped",
                        "summary": summary,
                        "command_center_actions": None,
                        "finalization": None,
                    }

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
                queue=queue,
                runtime_control=runtime_control,
                reason=reason,
                metadata=metadata,
            )
            if command_center_actions is None:
                return None

        summary = _iteration_summary(
            command_center_actions=command_center_actions,
            finalization=finalization,
            apply_command_center_actions=apply_command_center_actions,
            finalize_ready_tasks=finalize_ready_tasks,
        )
        if not dry_run:
            self._record_iteration(
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

    def _record_iteration(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        status: str,
        summary: dict[str, object],
    ) -> None:
        self._recorder.record_iteration(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=actor_user_id,
            status=status,
            summary=summary,
        )
