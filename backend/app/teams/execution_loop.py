from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.audit.service import AuditService
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.tasks.manager_diagnostics import TaskManagerDiagnosticsService
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TERMINAL_TASK_STATUSES, TaskStatus
from backend.app.teams.command_center import TeamCommandCenterService
from backend.app.teams.models import AgentTeam
from backend.app.workers.queue import RedisQueue

COMPLETED_STEP_STATUSES = {"completed", "cancelled", "canceled"}
ACTIVE_RUN_STATUSES = {
    RunStatus.QUEUED.value,
    RunStatus.RUNNING.value,
    RunStatus.WAITING_RUNTIME.value,
    RunStatus.WAITING_APPROVAL.value,
}


class TeamExecutionLoopService:
    """Advance team tasks when execution, handoff, and PM acceptance are complete."""

    def __init__(self, session: Session) -> None:
        self._session = session

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
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        if not self._team_exists(workspace_id=workspace_id, team_id=team_id):
            return None

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
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="team.execution_loop.iteration_ran",
                target_type="agent_team",
                target_id=team_id,
                metadata=summary,
            )
            self._session.commit()

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
        actor_user_id: UUID,
        dry_run: bool = True,
        max_tasks: int = 50,
    ) -> dict[str, object] | None:
        team = self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == team_id,
            )
        )
        if team is None:
            return None

        tasks = self._tasks(workspace_id=workspace_id, team_id=team_id, limit=max_tasks)
        results = [
            self._finalization_result(task=task, actor_user_id=actor_user_id, dry_run=dry_run)
            for task in tasks
        ]
        finalized = [item for item in results if item["status"] == "finalized"]
        if finalized and not dry_run:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="team.execution_loop.tasks_finalized",
                target_type="agent_team",
                target_id=team_id,
                metadata={
                    "finalized_task_ids": [str(item["task_id"]) for item in finalized],
                    "scanned_task_count": len(tasks),
                },
            )
        if not dry_run:
            self._session.commit()

        return {
            "workspace_id": workspace_id,
            "team_id": team_id,
            "generated_at": datetime.now(UTC),
            "dry_run": dry_run,
            "status": "dry_run" if dry_run else "finalized" if finalized else "noop",
            "scanned_task_count": len(tasks),
            "finalized_task_count": len(finalized),
            "skipped_task_count": len(results) - len(finalized),
            "results": results,
        }

    def _team_exists(self, *, workspace_id: UUID, team_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(AgentTeam.id).where(
                    AgentTeam.workspace_id == workspace_id,
                    AgentTeam.id == team_id,
                )
            )
            is not None
        )

    def _tasks(self, *, workspace_id: UUID, team_id: UUID, limit: int) -> list[Task]:
        return list(
            self._session.scalars(
                select(Task)
                .where(
                    Task.workspace_id == workspace_id,
                    Task.agent_team_id == team_id,
                    ~Task.status.in_([status.value for status in TERMINAL_TASK_STATUSES]),
                )
                .order_by(Task.priority.desc(), Task.updated_at.desc(), Task.id.asc())
                .limit(limit)
            )
        )

    def _finalization_result(
        self,
        *,
        task: Task,
        actor_user_id: UUID,
        dry_run: bool,
    ) -> dict[str, object]:
        blocked_reason = self._blocked_reason(task)
        if blocked_reason is not None:
            return _result(task, "skipped", blocked_reason)

        approval = self._latest_approved_acceptance(task)
        if approval is None:
            return _result(task, "skipped", "approved_acceptance_missing")

        final_output = _final_output_from_acceptance(approval)
        if dry_run:
            return _result(task, "would_finalize", "ready", final_output=final_output)

        TaskStateService().transition(
            task,
            TaskStatus.COMPLETED,
            completed_at=datetime.now(UTC),
            final_output=final_output,
        )
        AuditService(self._session).record_user_action(
            workspace_id=task.workspace_id,
            user_id=actor_user_id,
            action="task.execution_loop.finalized",
            target_type="task",
            target_id=task.id,
            metadata={
                "team_id": str(task.agent_team_id),
                "acceptance_message_id": str(approval.id),
            },
        )
        return _result(task, "finalized", "ready", final_output=final_output)

    def _blocked_reason(self, task: Task) -> str | None:
        if task.status != TaskStatus.RUNNING.value:
            return "task_status_not_finalizable"
        if self._has_incomplete_steps(task):
            return "team_steps_incomplete"
        if self._has_active_runs(task):
            return "active_runs_present"
        diagnostics = TaskManagerDiagnosticsService(self._session).get_diagnostics(
            workspace_id=task.workspace_id,
            task_id=task.id,
        )
        summary = (
            diagnostics.get("summary")
            if isinstance(diagnostics, dict) and isinstance(diagnostics.get("summary"), dict)
            else {}
        )
        if summary.get("status") != "healthy":
            return "manager_acceptance_not_healthy"
        return None

    def _has_incomplete_steps(self, task: Task) -> bool:
        count = self._session.scalar(
            select(TaskStep.id)
            .where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
                ~TaskStep.status.in_(COMPLETED_STEP_STATUSES),
            )
            .limit(1)
        )
        return count is not None

    def _has_active_runs(self, task: Task) -> bool:
        count = self._session.scalar(
            select(AgentRun.id)
            .where(
                AgentRun.workspace_id == task.workspace_id,
                AgentRun.task_id == task.id,
                AgentRun.status.in_(ACTIVE_RUN_STATUSES),
            )
            .limit(1)
        )
        return count is not None

    def _latest_approved_acceptance(self, task: Task) -> TaskMessage | None:
        messages = self._session.scalars(
            select(TaskMessage)
            .where(
                TaskMessage.workspace_id == task.workspace_id,
                TaskMessage.task_id == task.id,
                TaskMessage.message_type == "pm.acceptance_decision",
            )
            .order_by(TaskMessage.sequence.desc(), TaskMessage.created_at.desc())
        ).all()
        for message in messages:
            payload = message.payload if isinstance(message.payload, dict) else {}
            if payload.get("decision") == "approved":
                return message
        return None


def _result(
    task: Task,
    status: str,
    reason: str,
    *,
    final_output: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "task_id": task.id,
        "previous_status": task.status,
        "status": status,
        "reason": reason,
        "final_output": final_output,
    }


def _iteration_summary(
    *,
    command_center_actions: dict[str, object] | None,
    finalization: dict[str, object] | None,
    apply_command_center_actions: bool,
    finalize_ready_tasks: bool,
) -> dict[str, object]:
    return {
        "apply_command_center_actions": apply_command_center_actions,
        "finalize_ready_tasks": finalize_ready_tasks,
        "eligible_action_count": _int_from(command_center_actions, "eligible_action_count"),
        "applied_action_count": _int_from(command_center_actions, "applied_action_count"),
        "scheduled_run_count": _int_from(command_center_actions, "scheduled_run_count"),
        "scanned_task_count": _int_from(finalization, "scanned_task_count"),
        "finalized_task_count": _int_from(finalization, "finalized_task_count"),
        "skipped_task_count": _int_from(finalization, "skipped_task_count"),
    }


def _advanced(summary: dict[str, object]) -> bool:
    return any(
        _int(summary.get(key)) > 0
        for key in ("applied_action_count", "scheduled_run_count", "finalized_task_count")
    )


def _int_from(payload: dict[str, object] | None, key: str) -> int:
    if payload is None:
        return 0
    return _int(payload.get(key))


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _final_output_from_acceptance(message: TaskMessage) -> dict[str, object]:
    payload = message.payload if isinstance(message.payload, dict) else {}
    summary = payload.get("summary")
    return {
        "summary": summary if isinstance(summary, str) and summary else "Approved",
        "source": "pm_acceptance_decision",
        "acceptance_message_id": str(message.id),
        "decision": "approved",
    }
