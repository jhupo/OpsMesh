from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.observability.audit_service import AuditService
from backend.app.runs.models import AgentRun
from backend.app.tasks.manager_diagnostics import TaskManagerDiagnosticsService
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TERMINAL_TASK_STATUSES, TaskStatus
from backend.app.teams.execution_loop_constants import ACTIVE_RUN_STATUSES, COMPLETED_STEP_STATUSES
from backend.app.teams.execution_loop_payloads import _final_output_from_acceptance, _result
from backend.app.teams.models import AgentTeam


class TeamExecutionFinalizationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def finalize_ready_tasks(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID | None,
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
            self._record_finalization_action(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
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
        actor_user_id: UUID | None,
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
        self._record_finalization_action(
            workspace_id=task.workspace_id,
            actor_user_id=actor_user_id,
            action="task.execution_loop.finalized",
            target_type="task",
            target_id=task.id,
            metadata={
                "team_id": str(task.agent_team_id),
                "acceptance_message_id": str(approval.id),
            },
        )
        return _result(task, "finalized", "ready", final_output=final_output)

    def _record_finalization_action(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        action: str,
        target_type: str,
        target_id: UUID,
        metadata: dict[str, object],
    ) -> None:
        audit = AuditService(self._session)
        if actor_user_id is None:
            audit.record_system_action(
                workspace_id=workspace_id,
                actor_id="opsmesh.team_execution_loop",
                action=action,
                target_type=target_type,
                target_id=target_id,
                metadata=metadata,
            )
        else:
            audit.record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                metadata=metadata,
            )

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
        if diagnostics is None or diagnostics["summary"]["status"] != "healthy":
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
