from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.schemas.tasks import TaskCorrectionRequest, TaskDeliveryDecisionRequest
from backend.app.audit.service import AuditService
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.tasks.corrections import TaskCorrectionResult, TaskCorrectionService
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TaskStatus

ACTIVE_RUN_STATUSES = {
    RunStatus.QUEUED.value,
    RunStatus.RUNNING.value,
    RunStatus.WAITING_RUNTIME.value,
    RunStatus.WAITING_APPROVAL.value,
}
FINAL_STEP_STATUSES = {"completed", "cancelled"}


class TaskDeliveryDecisionService:
    """Apply owner/manager delivery acceptance decisions for a task."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def apply_decision(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        actor_user_id: UUID,
        request: TaskDeliveryDecisionRequest,
    ) -> dict[str, object] | None:
        task = self._task(workspace_id, task_id)
        if task is None:
            return None
        if request.action == "approve":
            return self._approve(task, actor_user_id=actor_user_id, request=request)
        if request.action in {"request_changes", "reject"}:
            return self._request_follow_up(task, actor_user_id=actor_user_id, request=request)
        raise ValueError(f"Unsupported delivery decision: {request.action}")

    def _approve(
        self,
        task: Task,
        *,
        actor_user_id: UUID,
        request: TaskDeliveryDecisionRequest,
    ) -> dict[str, object]:
        decision_message = self._append_decision_message(
            task,
            actor_user_id=actor_user_id,
            decision="approved",
            request=request,
        )
        final_output = _final_output_from_decision(decision_message)
        finalization = self._finalization_result(task, request.finalize, final_output)
        self._audit(
            task,
            actor_user_id=actor_user_id,
            action="task.delivery.approved",
            metadata={
                "message_id": str(decision_message.id),
                "finalization": finalization,
                "metadata": request.metadata,
            },
        )
        self._session.commit()
        return self._response(
            task,
            request=request,
            decision="approved",
            status="approved",
            message_id=decision_message.id,
            final_output=final_output,
            details={"finalization": finalization},
        )

    def _request_follow_up(
        self,
        task: Task,
        *,
        actor_user_id: UUID,
        request: TaskDeliveryDecisionRequest,
    ) -> dict[str, object]:
        if not request.instruction:
            raise ValueError("instruction is required for follow-up delivery decisions")
        decision = "request_revision" if request.action == "request_changes" else "rejected"
        decision_message = self._append_decision_message(
            task,
            actor_user_id=actor_user_id,
            decision=decision,
            request=request,
        )
        if task.status not in {"completed", "failed", "cancelled"}:
            TaskStateService().transition(task, TaskStatus.BLOCKED)
        correction = self._create_follow_up_correction(
            task,
            actor_user_id=actor_user_id,
            request=request,
            decision_message_id=decision_message.id,
        )
        self._audit(
            task,
            actor_user_id=actor_user_id,
            action=f"task.delivery.{request.action}",
            metadata={
                "message_id": str(decision_message.id),
                "created_step_id": str(correction.created_step_id)
                if correction is not None and correction.created_step_id is not None
                else None,
                "metadata": request.metadata,
            },
        )
        self._session.commit()
        return self._response(
            task,
            request=request,
            decision=decision,
            status="follow_up_created" if correction is not None else "recorded",
            message_id=decision_message.id,
            created_step_id=correction.created_step_id if correction is not None else None,
            details={
                "correction_status": correction.status if correction is not None else None,
                "correction_mode": correction.mode if correction is not None else None,
            },
        )

    def _task(self, workspace_id: UUID, task_id: UUID) -> Task | None:
        return self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )

    def _finalization_result(
        self,
        task: Task,
        finalize: bool,
        final_output: dict[str, object],
    ) -> dict[str, object]:
        if not finalize:
            return {"status": "deferred", "reason": "finalize_disabled"}
        blocked_reason = self._finalization_blocked_reason(task)
        if blocked_reason is not None:
            return {"status": "deferred", "reason": blocked_reason}
        TaskStateService().transition(
            task,
            TaskStatus.COMPLETED,
            completed_at=datetime.now(UTC),
            final_output=final_output,
        )
        return {"status": "finalized", "reason": "ready"}

    def _finalization_blocked_reason(self, task: Task) -> str | None:
        if task.status not in {TaskStatus.RUNNING.value, TaskStatus.BLOCKED.value}:
            return "task_status_not_finalizable"
        active_run = self._session.scalar(
            select(AgentRun.id)
            .where(
                AgentRun.workspace_id == task.workspace_id,
                AgentRun.task_id == task.id,
                AgentRun.status.in_(ACTIVE_RUN_STATUSES),
            )
            .limit(1)
        )
        if active_run is not None:
            return "active_runs_present"
        incomplete_step = self._session.scalar(
            select(TaskStep.id)
            .where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
                ~TaskStep.status.in_(FINAL_STEP_STATUSES),
            )
            .limit(1)
        )
        if incomplete_step is not None:
            return "task_steps_incomplete"
        return None

    def _create_follow_up_correction(
        self,
        task: Task,
        *,
        actor_user_id: UUID,
        request: TaskDeliveryDecisionRequest,
        decision_message_id: UUID,
    ) -> TaskCorrectionResult | None:
        correction = TaskCorrectionRequest(
            target_type=request.target_type or "task",
            mode=request.correction_mode or "revise",
            instruction=request.instruction or request.summary,
            target_id=request.target_id,
            metadata={
                **request.metadata,
                "source": "delivery_decision",
                "decision": request.action,
                "acceptance_message_id": str(decision_message_id),
            },
        )
        return TaskCorrectionService(self._session).create_correction(
            workspace_id=task.workspace_id,
            task_id=task.id,
            actor_user_id=actor_user_id,
            request=correction,
        )

    def _append_decision_message(
        self,
        task: Task,
        *,
        actor_user_id: UUID,
        decision: str,
        request: TaskDeliveryDecisionRequest,
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
            message_type="pm.acceptance_decision",
            sequence=sequence,
            body=request.summary,
            payload={
                "decision": decision,
                "summary": request.summary,
                "instruction": request.instruction,
                "actor_user_id": str(actor_user_id),
                "source": "delivery_decision",
                "metadata": request.metadata,
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
        request: TaskDeliveryDecisionRequest,
        decision: str,
        status: str,
        message_id: UUID,
        created_step_id: UUID | None = None,
        final_output: dict[str, object] | None = None,
        details: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self._session.flush()
        return {
            "workspace_id": task.workspace_id,
            "task_id": task.id,
            "action": request.action,
            "decision": decision,
            "status": status,
            "task_status": task.status,
            "message_id": message_id,
            "created_step_id": created_step_id,
            "final_output": final_output,
            "details": details or {},
        }


def _final_output_from_decision(message: TaskMessage) -> dict[str, object]:
    payload = message.payload if isinstance(message.payload, dict) else {}
    summary = payload.get("summary")
    return {
        "summary": summary if isinstance(summary, str) and summary else "Approved",
        "source": "delivery_decision",
        "acceptance_message_id": str(message.id),
        "decision": "approved",
    }
