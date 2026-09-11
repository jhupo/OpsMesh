from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.tasks import TaskControlActionRequest, TaskCorrectionRequest
from backend.app.audit.service import AuditService
from backend.app.core.typing import int_or_zero
from backend.app.orchestration.run_control import RunControlService
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.tasks import control_execution
from backend.app.tasks.control_messages import TaskControlMessageWriter
from backend.app.tasks.control_payloads import task_control_response
from backend.app.tasks.control_state import task_control_state, with_task_control_state
from backend.app.tasks.corrections import TaskCorrectionService
from backend.app.tasks.models import Task
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TERMINAL_TASK_STATUSES, TaskStatus
from backend.app.workers.queue.redis_queue import RedisQueue

TASK_PAUSED_REASON = control_execution.TASK_PAUSED_REASON


class TaskControlService:
    """Unified owner/operator controls for interrupting and steering task execution."""

    def __init__(self, session: Session, queue: RedisQueue | None = None) -> None:
        self._session = session
        self._queue = queue

    def apply_action(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        actor_user_id: UUID,
        request: TaskControlActionRequest,
    ) -> dict[str, object] | None:
        task = self._task(workspace_id, task_id)
        if task is None:
            return None
        action = request.action
        if action == "pause":
            return self._pause_task(task, actor_user_id=actor_user_id, request=request)
        if action == "resume":
            return self._resume_task(task, actor_user_id=actor_user_id, request=request)
        if action == "add_instruction":
            return self._add_instruction(task, actor_user_id=actor_user_id, request=request)
        if action == "create_correction":
            return self._create_correction(task, actor_user_id=actor_user_id, request=request)
        if action == "cancel":
            return self._cancel_task(task, actor_user_id=actor_user_id, request=request)
        raise ValueError(f"Unsupported task control action: {action}")

    def _pause_task(
        self,
        task: Task,
        *,
        actor_user_id: UUID,
        request: TaskControlActionRequest,
    ) -> dict[str, object]:
        if TaskStatus(task.status) in TERMINAL_TASK_STATUSES:
            raise ValueError("Terminal tasks cannot be paused")
        now = datetime.now(UTC)
        execution = control_execution.TaskControlExecutionService(self._session, queue=self._queue)
        cancelled_runs, worker_cancel_requests = execution.cancel_active_runs(task, now=now)
        blocked_steps = execution.block_schedulable_steps(task, request=request, now=now)
        previous_status = task.status
        if TaskStatus(task.status) != TaskStatus.BLOCKED:
            TaskStateService().transition(task, TaskStatus.BLOCKED)
        control = task_control_state(task)
        control.update(
            {
                "paused": True,
                "pause_reason": request.reason or "user_paused",
                "paused_at": now.isoformat(),
                "paused_by_user_id": str(actor_user_id),
            }
        )
        task.generic_state = with_task_control_state(task.generic_state, control)
        message = TaskControlMessageWriter(self._session).append_control_message(
            task,
            actor_user_id=actor_user_id,
            action="pause",
            instruction=request.instruction,
            reason=request.reason,
            metadata={
                **request.metadata,
                "cancelled_run_count": cancelled_runs,
                "worker_cancel_request_count": worker_cancel_requests,
                "blocked_step_count": blocked_steps,
            },
        )
        self._audit(
            task,
            actor_user_id=actor_user_id,
            action="task.control.paused",
            metadata={
                "previous_status": previous_status,
                "cancelled_run_count": cancelled_runs,
                "worker_cancel_request_count": worker_cancel_requests,
                "blocked_step_count": blocked_steps,
                "reason": request.reason,
            },
        )
        self._session.commit()
        return self._response(
            task,
            request=request,
            status="applied",
            message_id=message.id,
            details={
                "previous_status": previous_status,
                "cancelled_run_count": cancelled_runs,
                "worker_cancel_request_count": worker_cancel_requests,
                "blocked_step_count": blocked_steps,
            },
        )

    def _resume_task(
        self,
        task: Task,
        *,
        actor_user_id: UUID,
        request: TaskControlActionRequest,
    ) -> dict[str, object]:
        if TaskStatus(task.status) in TERMINAL_TASK_STATUSES:
            raise ValueError("Terminal tasks cannot be resumed")
        control = task_control_state(task)
        was_paused = control.get("paused") is True
        control.update(
            {
                "paused": False,
                "resumed_at": datetime.now(UTC).isoformat(),
                "resumed_by_user_id": str(actor_user_id),
            }
        )
        control.pop("pause_reason", None)
        task.generic_state = with_task_control_state(task.generic_state, control)
        execution = control_execution.TaskControlExecutionService(self._session, queue=self._queue)
        unblocked_steps = execution.unblock_paused_steps(task)
        previous_status = task.status
        if TaskStatus(task.status) == TaskStatus.BLOCKED:
            TaskStateService().transition(task, TaskStatus.RUNNING)
        scheduled_runs = execution.schedule_task_work(
            task,
            actor_user_id,
            enqueue=request.enqueue,
        )
        message = TaskControlMessageWriter(self._session).append_control_message(
            task,
            actor_user_id=actor_user_id,
            action="resume",
            instruction=request.instruction,
            reason=request.reason,
            metadata={
                **request.metadata,
                "was_paused": was_paused,
                "unblocked_step_count": unblocked_steps,
                "scheduled_run_count": len(scheduled_runs),
            },
        )
        self._audit(
            task,
            actor_user_id=actor_user_id,
            action="task.control.resumed",
            metadata={
                "previous_status": previous_status,
                "was_paused": was_paused,
                "unblocked_step_count": unblocked_steps,
                "scheduled_run_count": len(scheduled_runs),
                "reason": request.reason,
            },
        )
        self._session.commit()
        return self._response(
            task,
            request=request,
            status="applied",
            message_id=message.id,
            scheduled_run_ids=[run.id for run in scheduled_runs],
            details={
                "previous_status": previous_status,
                "was_paused": was_paused,
                "unblocked_step_count": unblocked_steps,
                "scheduled_run_count": len(scheduled_runs),
            },
        )

    def _add_instruction(
        self,
        task: Task,
        *,
        actor_user_id: UUID,
        request: TaskControlActionRequest,
    ) -> dict[str, object]:
        if not request.instruction:
            raise ValueError("instruction is required")
        control = task_control_state(task)
        control["instruction_count"] = int_or_zero(control.get("instruction_count")) + 1
        control["last_instruction_at"] = datetime.now(UTC).isoformat()
        task.generic_state = with_task_control_state(task.generic_state, control)
        message = TaskControlMessageWriter(self._session).append_control_message(
            task,
            actor_user_id=actor_user_id,
            action="add_instruction",
            instruction=request.instruction,
            reason=request.reason,
            metadata=request.metadata,
        )
        self._audit(
            task,
            actor_user_id=actor_user_id,
            action="task.control.instruction_added",
            metadata={"message_id": str(message.id), "reason": request.reason},
        )
        self._session.commit()
        return self._response(
            task,
            request=request,
            status="applied",
            message_id=message.id,
            details={"instruction_count": control["instruction_count"]},
        )

    def _create_correction(
        self,
        task: Task,
        *,
        actor_user_id: UUID,
        request: TaskControlActionRequest,
    ) -> dict[str, object] | None:
        if not request.instruction:
            raise ValueError("instruction is required")
        correction = TaskCorrectionRequest(
            target_type=request.target_type or "task",
            mode=request.correction_mode or "revise",
            instruction=request.instruction,
            target_id=request.target_id,
            metadata={
                **request.metadata,
                "source": "task_control",
                "reason": request.reason,
            },
        )
        result = TaskCorrectionService(self._session, queue=self._queue).create_correction(
            workspace_id=task.workspace_id,
            task_id=task.id,
            actor_user_id=actor_user_id,
            request=correction,
        )
        if result is None:
            return None
        return self._response(
            task,
            request=request,
            status="applied",
            message_id=result.message_id,
            changed_step_ids=[result.created_step_id] if result.created_step_id is not None else [],
            details={
                "correction_mode": result.mode,
                "correction_target_type": result.target_type,
                "created_step_id": str(result.created_step_id)
                if result.created_step_id is not None
                else None,
            },
        )

    def _cancel_task(
        self,
        task: Task,
        *,
        actor_user_id: UUID,
        request: TaskControlActionRequest,
    ) -> dict[str, object] | None:
        cancelled = RunControlService(
            session=self._session,
            enqueue_run=RunOrchestrationService(self._session, queue=self._queue).enqueue_run,
        ).cancel_task(
            workspace_id=task.workspace_id,
            task_id=task.id,
            actor_user_id=actor_user_id,
        )
        if cancelled is None:
            return None
        return self._response(
            cancelled,
            request=request,
            status="applied",
            details={"cancelled": True, "reason": request.reason},
        )

    def _task(self, workspace_id: UUID, task_id: UUID) -> Task | None:
        return self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )

    def _audit(
        self,
        task: Task,
        *,
        actor_user_id: UUID,
        action: str,
        metadata: dict[str, object],
    ) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=task.workspace_id,
            user_id=actor_user_id,
            action=action,
            target_type="task",
            target_id=task.id,
            metadata=metadata,
        )

    def _response(
        self,
        task: Task,
        *,
        request: TaskControlActionRequest,
        status: str,
        message_id: UUID | None = None,
        changed_step_ids: list[UUID] | None = None,
        scheduled_run_ids: list[UUID] | None = None,
        details: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self._session.flush()
        return task_control_response(
            task,
            request=request,
            status=status,
            message_id=message_id,
            changed_step_ids=changed_step_ids,
            scheduled_run_ids=scheduled_run_ids,
            details=details,
        )
