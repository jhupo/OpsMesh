from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.operations.models import WorkerLease
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.tasks.control import TASK_PAUSED_REASON
from backend.app.tasks.control_state import task_control_state
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.tasks.status import TERMINAL_TASK_STATUSES, TaskStatus
from backend.app.workers.jobs import JobType

ACTIVE_RUN_STATUSES = {
    RunStatus.QUEUED.value,
    RunStatus.RUNNING.value,
    RunStatus.WAITING_RUNTIME.value,
    RunStatus.WAITING_APPROVAL.value,
    RunStatus.WAITING_SUBWORKFLOW.value,
}


class TaskControlDiagnosticsService:
    """Explain task interruption, correction, and resume side effects."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_diagnostics(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        message_limit: int = 20,
    ) -> dict[str, object] | None:
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            return None

        steps = list(
            self._session.scalars(
                select(TaskStep)
                .where(TaskStep.workspace_id == workspace_id, TaskStep.task_id == task_id)
                .order_by(TaskStep.order_index.asc(), TaskStep.id.asc())
            ).all()
        )
        runs = list(
            self._session.scalars(
                select(AgentRun)
                .where(AgentRun.workspace_id == workspace_id, AgentRun.task_id == task_id)
                .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
            ).all()
        )
        messages = self._control_messages(workspace_id, task_id, message_limit)
        control = task_control_state(task)
        paused_steps = [step for step in steps if _is_task_control_paused_step(step)]
        cancelled_runs = [run for run in runs if _is_pause_cancelled_run(run)]
        scheduled_runs = [run for run in runs if _is_resume_scheduled_run(run)]
        active_runs = [run for run in runs if run.status in ACTIVE_RUN_STATUSES]
        worker_cancel_requests = self._worker_cancel_requests(
            workspace_id,
            [run.id for run in runs],
        )
        summary = {
            "task_status": task.status,
            "paused": control.get("paused") is True,
            "active_run_count": len(active_runs),
            "paused_blocked_step_count": len(paused_steps),
            "cancelled_by_pause_run_count": len(cancelled_runs),
            "worker_cancel_request_count": len(worker_cancel_requests),
            "scheduled_resume_run_count": len(scheduled_runs),
            "control_message_count": sum(
                1 for message in messages if message.message_type.startswith("task.control.")
            ),
            "correction_message_count": sum(
                1 for message in messages if message.message_type == "task.correction.created"
            ),
            "latest_control_sequence": max(
                (message.sequence for message in messages),
                default=0,
            ),
        }
        return {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "generated_at": datetime.now(UTC),
            "status": _status(task, summary),
            "control": control,
            "summary": summary,
            "paused_steps": [_step_payload(step) for step in paused_steps],
            "cancelled_runs": [_run_payload(run) for run in cancelled_runs],
            "scheduled_runs": [_run_payload(run) for run in scheduled_runs],
            "active_runs": [_run_payload(run) for run in active_runs],
            "worker_cancel_requests": [
                _worker_cancel_request_payload(lease) for lease in worker_cancel_requests
            ],
            "recent_control_messages": [_message_payload(message) for message in messages],
            "recommended_actions": _recommended_actions(task, summary),
        }

    def _control_messages(
        self,
        workspace_id: UUID,
        task_id: UUID,
        limit: int,
    ) -> list[TaskMessage]:
        return list(
            self._session.scalars(
                select(TaskMessage)
                .where(
                    TaskMessage.workspace_id == workspace_id,
                    TaskMessage.task_id == task_id,
                    or_(
                        TaskMessage.message_type.like("task.control.%"),
                        TaskMessage.message_type == "task.correction.created",
                    ),
                )
                .order_by(TaskMessage.sequence.desc())
                .limit(limit)
            ).all()
        )

    def _worker_cancel_requests(
        self,
        workspace_id: UUID,
        run_ids: list[UUID],
    ) -> list[WorkerLease]:
        if not run_ids:
            return []
        leases = self._session.scalars(
            select(WorkerLease)
            .where(
                WorkerLease.workspace_id == workspace_id,
                WorkerLease.job_type == JobType.AGENT_RUN.value,
                WorkerLease.resource_id.in_(run_ids),
            )
            .order_by(WorkerLease.started_at.desc(), WorkerLease.created_at.desc())
        ).all()
        return [
            lease
            for lease in leases
            if isinstance(lease.lease_metadata, dict)
            and lease.lease_metadata.get("cancel_requested") is True
        ]


def _status(task: Task, summary: dict[str, object]) -> str:
    if TaskStatus(task.status) in TERMINAL_TASK_STATUSES:
        return task.status
    if summary.get("paused") is True:
        return "paused"
    if _int(summary.get("paused_blocked_step_count")) > 0:
        return "resume_incomplete"
    if _int(summary.get("active_run_count")) > 0:
        return "active"
    if _int(summary.get("scheduled_resume_run_count")) > 0:
        return "resumed"
    return "idle"


def _recommended_actions(task: Task, summary: dict[str, object]) -> list[dict[str, object]]:
    if TaskStatus(task.status) in TERMINAL_TASK_STATUSES:
        return []
    actions: list[dict[str, object]] = []
    if summary.get("paused") is True or _int(summary.get("paused_blocked_step_count")) > 0:
        actions.append(
            {
                "action": "resume",
                "reason": "task_control_pause_active",
                "api_route": "POST /api/v1/workspaces/{workspace_id}/tasks/{task_id}/control",
                "payload_template": {"action": "resume", "enqueue": True},
            }
        )
    if _int(summary.get("active_run_count")) > 0:
        actions.append({"action": "monitor_active_runs", "reason": "task_has_active_runs"})
    if _int(summary.get("correction_message_count")) > 0:
        actions.append({"action": "review_corrections", "reason": "correction_requested"})
    if not actions:
        actions.append({"action": "inspect_execution_status", "reason": "no_control_blockers"})
    return actions


def _is_task_control_paused_step(step: TaskStep) -> bool:
    dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
    return step.status == "blocked" and dependencies.get("task_control_paused") is True


def _is_pause_cancelled_run(run: AgentRun) -> bool:
    error = run.error if isinstance(run.error, dict) else {}
    return run.status == RunStatus.CANCELLED.value and error.get("code") == TASK_PAUSED_REASON


def _is_resume_scheduled_run(run: AgentRun) -> bool:
    payload = run.input if isinstance(run.input, dict) else {}
    return payload.get("source") == "task_control_resume"


def _step_payload(step: TaskStep) -> dict[str, object]:
    dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
    return {
        "task_step_id": step.id,
        "work_package_id": step.work_package_id,
        "title": step.title,
        "status": step.status,
        "blocked_reason": dependencies.get("blocked_reason"),
        "blocked_at": dependencies.get("blocked_at"),
        "pause_reason": dependencies.get("pause_reason"),
        "dependencies": dependencies,
    }


def _run_payload(run: AgentRun) -> dict[str, object]:
    return {
        "id": run.id,
        "status": run.status,
        "task_step_id": run.task_step_id,
        "agent_profile_id": run.agent_profile_id,
        "runtime_id": run.runtime_id,
        "runtime_space_id": run.runtime_space_id,
        "model": run.model,
        "input": run.input,
        "error": run.error,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "created_at": run.created_at,
    }


def _message_payload(message: TaskMessage) -> dict[str, object]:
    return {
        "id": message.id,
        "sequence": message.sequence,
        "message_type": message.message_type,
        "body": message.body,
        "payload": message.payload,
        "created_at": message.created_at,
    }


def _worker_cancel_request_payload(lease: WorkerLease) -> dict[str, object]:
    metadata = lease.lease_metadata if isinstance(lease.lease_metadata, dict) else {}
    lifecycle_events = metadata.get("lifecycle_events")
    return {
        "agent_run_id": lease.resource_id,
        "worker_id": lease.worker_id,
        "queue_name": lease.queue_name,
        "status": lease.status,
        "attempt": lease.attempt,
        "started_at": lease.started_at,
        "finished_at": lease.finished_at,
        "cancel_requested": metadata.get("cancel_requested") is True,
        "cancel_requested_at": metadata.get("cancel_requested_at"),
        "last_lifecycle_event": metadata.get("last_lifecycle_event")
        if isinstance(metadata.get("last_lifecycle_event"), dict)
        else None,
        "lifecycle_event_count": len(lifecycle_events)
        if isinstance(lifecycle_events, list)
        else 0,
    }


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0
