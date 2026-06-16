from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.schemas.tasks import TaskControlActionRequest, TaskCorrectionRequest
from backend.app.audit.service import AuditService
from backend.app.orchestration.run_control import RunControlService
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus, require_run_transition
from backend.app.tasks.corrections import TaskCorrectionService
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TERMINAL_TASK_STATUSES, TaskStatus
from backend.app.workers.lease_lifecycle import mark_agent_run_worker_cancel_requested
from backend.app.workers.queue.redis_queue import RedisQueue

PAUSABLE_RUN_STATUSES = {
    RunStatus.QUEUED.value,
    RunStatus.RUNNING.value,
    RunStatus.WAITING_RUNTIME.value,
    RunStatus.WAITING_APPROVAL.value,
}
PAUSABLE_STEP_STATUSES = {"queued", "running", "blocked"}
TASK_PAUSED_REASON = "task_paused"


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
        cancelled_runs, worker_cancel_requests = self._cancel_active_runs(task, now=now)
        blocked_steps = self._block_schedulable_steps(task, request=request, now=now)
        previous_status = task.status
        if TaskStatus(task.status) != TaskStatus.BLOCKED:
            TaskStateService().transition(task, TaskStatus.BLOCKED)
        control = _control_state(task)
        control.update(
            {
                "paused": True,
                "pause_reason": request.reason or "user_paused",
                "paused_at": now.isoformat(),
                "paused_by_user_id": str(actor_user_id),
            }
        )
        task.generic_state = _with_control_state(task.generic_state, control)
        message = self._append_control_message(
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
        control = _control_state(task)
        was_paused = control.get("paused") is True
        control.update(
            {
                "paused": False,
                "resumed_at": datetime.now(UTC).isoformat(),
                "resumed_by_user_id": str(actor_user_id),
            }
        )
        control.pop("pause_reason", None)
        task.generic_state = _with_control_state(task.generic_state, control)
        unblocked_steps = self._unblock_paused_steps(task)
        previous_status = task.status
        if TaskStatus(task.status) == TaskStatus.BLOCKED:
            TaskStateService().transition(task, TaskStatus.RUNNING)
        scheduled_runs = self._schedule_task_work(task, actor_user_id, enqueue=request.enqueue)
        message = self._append_control_message(
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
        control = _control_state(task)
        control["instruction_count"] = int(control.get("instruction_count") or 0) + 1
        control["last_instruction_at"] = datetime.now(UTC).isoformat()
        task.generic_state = _with_control_state(task.generic_state, control)
        message = self._append_control_message(
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
    ) -> dict[str, object]:
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
        result = TaskCorrectionService(self._session).create_correction(
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
    ) -> dict[str, object]:
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

    def _cancel_active_runs(self, task: Task, *, now: datetime) -> tuple[int, int]:
        runs = self._session.scalars(
            select(AgentRun).where(
                AgentRun.workspace_id == task.workspace_id,
                AgentRun.task_id == task.id,
                AgentRun.status.in_(PAUSABLE_RUN_STATUSES),
            )
        ).all()
        worker_cancel_requests = 0
        for run in runs:
            require_run_transition(RunStatus(run.status), RunStatus.CANCELLED)
            run.status = RunStatus.CANCELLED.value
            run.completed_at = now
            run.error = {"code": TASK_PAUSED_REASON, "message": "Task paused by owner control"}
            worker_cancel_requests += mark_agent_run_worker_cancel_requested(
                self._session,
                workspace_id=run.workspace_id,
                run_id=run.id,
                requested_at=now,
            )
        return len(runs), worker_cancel_requests

    def _block_schedulable_steps(
        self,
        task: Task,
        *,
        request: TaskControlActionRequest,
        now: datetime,
    ) -> int:
        steps = self._session.scalars(
            select(TaskStep).where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
                TaskStep.status.in_(PAUSABLE_STEP_STATUSES),
            )
        ).all()
        for step in steps:
            dependencies = dict(step.dependencies or {})
            dependencies.update(
                {
                    "task_control_paused": True,
                    "blocked_reason": TASK_PAUSED_REASON,
                    "blocked_at": now.isoformat(),
                    "blocked_by": "task_control",
                    "pause_reason": request.reason,
                }
            )
            step.dependencies = dependencies
            step.status = "blocked"
        return len(steps)

    def _unblock_paused_steps(self, task: Task) -> int:
        steps = self._session.scalars(
            select(TaskStep).where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
                TaskStep.status == "blocked",
            )
        ).all()
        changed = 0
        for step in steps:
            dependencies = dict(step.dependencies or {})
            if dependencies.get("task_control_paused") is not True:
                continue
            for key in (
                "task_control_paused",
                "blocked_reason",
                "blocked_at",
                "blocked_by",
                "pause_reason",
            ):
                dependencies.pop(key, None)
            step.dependencies = dependencies
            step.status = "queued"
            changed += 1
        return changed

    def _schedule_task_work(
        self,
        task: Task,
        actor_user_id: UUID,
        *,
        enqueue: bool,
    ) -> list[AgentRun]:
        if not enqueue:
            return []
        orchestrator = RunOrchestrationService(self._session, queue=self._queue)
        if task.agent_team_id is not None:
            return orchestrator.schedule_team_steps(
                workspace_id=task.workspace_id,
                team_id=task.agent_team_id,
                requested_by_user_id=actor_user_id,
            )
        run = AgentRun(
            workspace_id=task.workspace_id,
            task_id=task.id,
            runtime_space_id=task.runtime_space_id,
            status=RunStatus.QUEUED.value,
            input={
                "task_id": str(task.id),
                "title": task.title,
                "source": "task_control_resume",
            },
        )
        self._session.add(run)
        self._session.flush()
        orchestrator.enqueue_run(run, actor_user_id)
        return [run]

    def _append_control_message(
        self,
        task: Task,
        *,
        actor_user_id: UUID,
        action: str,
        instruction: str | None,
        reason: str | None,
        metadata: dict[str, object],
    ) -> TaskMessage:
        sequence = int(
            self._session.scalar(
                select(func.coalesce(func.max(TaskMessage.sequence), 0)).where(
                    TaskMessage.workspace_id == task.workspace_id,
                    TaskMessage.task_id == task.id,
                )
            )
            or 0
        ) + 1
        message = TaskMessage(
            workspace_id=task.workspace_id,
            task_id=task.id,
            message_type=f"task.control.{action}",
            sequence=sequence,
            body=instruction or reason or action,
            payload={
                "action": action,
                "instruction": instruction,
                "reason": reason,
                "actor_user_id": str(actor_user_id),
                "metadata": metadata,
            },
        )
        self._session.add(message)
        self._session.flush()
        return message

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
        return {
            "workspace_id": task.workspace_id,
            "task_id": task.id,
            "action": request.action,
            "status": status,
            "task_status": task.status,
            "message_id": message_id,
            "changed_step_ids": changed_step_ids or [],
            "scheduled_run_ids": scheduled_run_ids or [],
            "details": details or {},
        }


def _control_state(task: Task) -> dict[str, object]:
    state = task.generic_state if isinstance(task.generic_state, dict) else {}
    control = state.get("control")
    return dict(control) if isinstance(control, dict) else {}


def _with_control_state(
    generic_state: dict[str, object],
    control: dict[str, object],
) -> dict[str, object]:
    state = dict(generic_state or {})
    state["control"] = control
    return state
