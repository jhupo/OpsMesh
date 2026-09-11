from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.schemas.tasks import TaskCorrectionRequest
from backend.app.artifacts.models import Artifact
from backend.app.observability.audit_service import AuditService
from backend.app.tasks.message_append import TaskMessageAppendService
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TaskStatus
from backend.app.workers.queue.redis_queue import RedisQueue

STEP_STATUS_QUEUED = "queued"


@dataclass(frozen=True)
class TaskCorrectionResult:
    task_id: UUID
    mode: str
    target_type: str
    created_step_id: UUID | None
    message_id: UUID
    status: str
    scheduled_run_ids: tuple[UUID, ...] = ()


class TaskCorrectionService:
    def __init__(self, session: Session, queue: RedisQueue | None = None) -> None:
        self._session = session
        self._queue = queue

    def create_correction(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        actor_user_id: UUID,
        request: TaskCorrectionRequest,
    ) -> TaskCorrectionResult | None:
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            return None

        target_payload = self._resolve_target(task, request)
        created_step = None
        if request.mode == "stop_work":
            TaskStateService().transition(task, TaskStatus.CANCELLED)
        else:
            created_step = self._create_follow_up_step(task, request, target_payload)
            if task.status in {"completed", "failed", "cancelled"}:
                TaskStateService().reset_to_draft(task)
                TaskStateService().transition(task, TaskStatus.QUEUED)
                TaskStateService().transition(task, TaskStatus.RUNNING)
            elif task.status == TaskStatus.BLOCKED.value:
                TaskStateService().transition(task, TaskStatus.RUNNING)

        message = self._append_message(
            task,
            request,
            actor_user_id=actor_user_id,
            target_payload=target_payload,
            created_step_id=created_step.id if created_step is not None else None,
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="task.correction.created",
            target_type="task",
            target_id=task.id,
            metadata={
                "mode": request.mode,
                "target_type": request.target_type,
                "target_id": str(request.target_id) if request.target_id is not None else None,
                "created_step_id": str(created_step.id) if created_step is not None else None,
            },
        )
        self._session.commit()
        scheduled_run_ids = self._schedule_follow_up(task, actor_user_id)
        if scheduled_run_ids:
            self._session.commit()
        self._session.refresh(message)
        self._session.refresh(task)
        if created_step is not None:
            self._session.refresh(created_step)
        return TaskCorrectionResult(
            task_id=task.id,
            mode=request.mode,
            target_type=request.target_type,
            created_step_id=created_step.id if created_step is not None else None,
            message_id=message.id,
            status=task.status,
            scheduled_run_ids=tuple(scheduled_run_ids),
        )

    def _schedule_follow_up(self, task: Task, actor_user_id: UUID) -> list[UUID]:
        if self._queue is None or task.status not in {
            TaskStatus.RUNNING.value,
            TaskStatus.QUEUED.value,
        }:
            return []
        # Scheduling is deliberately delegated to the existing workspace scheduler so
        # corrections use the same dependency, capacity, quota, and idempotency gates.
        from backend.app.orchestration.runs import RunOrchestrationService

        runs = RunOrchestrationService(self._session, queue=self._queue).schedule_workspace_steps(
            workspace_id=task.workspace_id,
            requested_by_user_id=actor_user_id,
        )
        return [run.id for run in runs]

    def _resolve_target(
        self,
        task: Task,
        request: TaskCorrectionRequest,
    ) -> dict[str, object]:
        if request.target_type in {"task", "final_output"}:
            if request.target_id is not None and request.target_id != task.id:
                raise ValueError("Correction target does not belong to this task")
            return {"target_type": request.target_type, "task_id": str(task.id)}
        if request.target_id is None:
            raise ValueError(f"{request.target_type} correction requires target_id")
        if request.target_type == "step":
            step = self._require_step(task, request.target_id)
            return {
                "target_type": "step",
                "task_step_id": str(step.id),
                "work_package_id": step.work_package_id,
                "title": step.title,
                "agent_profile_id": (
                    str(step.assigned_agent_profile_id)
                    if step.assigned_agent_profile_id is not None
                    else None
                ),
            }
        if request.target_type == "agent":
            agent = self._require_agent(task.workspace_id, request.target_id)
            return {
                "target_type": "agent",
                "agent_profile_id": str(agent.id),
                "name": agent.name,
                "role": agent.role,
            }
        if request.target_type == "artifact":
            artifact = self._require_artifact(task, request.target_id)
            return {
                "target_type": "artifact",
                "artifact_id": str(artifact.id),
                "filename": artifact.filename,
                "artifact_type": artifact.artifact_type,
            }
        raise ValueError("Unsupported correction target")

    def _create_follow_up_step(
        self,
        task: Task,
        request: TaskCorrectionRequest,
        target_payload: dict[str, object],
    ) -> TaskStep:
        order_index = self._next_step_order(task)
        title = _step_title(request)
        assigned_agent_profile_id = _assigned_agent_profile_id(task, target_payload)
        step = TaskStep(
            workspace_id=task.workspace_id,
            task_id=task.id,
            runtime_space_id=task.runtime_space_id,
            work_package_id=f"correction-{request.mode}-{order_index}",
            title=title,
            description=request.instruction,
            assigned_agent_profile_id=assigned_agent_profile_id,
            status=STEP_STATUS_QUEUED,
            order_index=order_index,
            expected_artifacts=_expected_artifacts(request),
            acceptance_criteria=[request.instruction],
            review_policy={"reviewer": "manager", "mode": "user_correction_review"},
            dependencies={
                "correction": {
                    "mode": request.mode,
                    "target": target_payload,
                    "instruction": request.instruction,
                    "metadata": request.metadata,
                }
            },
        )
        self._session.add(step)
        self._session.flush()
        return step

    def _append_message(
        self,
        task: Task,
        request: TaskCorrectionRequest,
        *,
        actor_user_id: UUID,
        target_payload: dict[str, object],
        created_step_id: UUID | None,
    ) -> TaskMessage:
        return TaskMessageAppendService(self._session).append_for_task(
            task,
            task_step_id=created_step_id,
            message_type="task.correction.created",
            body=request.instruction,
            payload={
                "mode": request.mode,
                "target_type": request.target_type,
                "target": target_payload,
                "created_step_id": str(created_step_id) if created_step_id is not None else None,
                "actor_user_id": str(actor_user_id),
                "metadata": request.metadata,
            },
        )

    def _require_step(self, task: Task, step_id: UUID) -> TaskStep:
        step = self._session.scalar(
            select(TaskStep).where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
                TaskStep.id == step_id,
            )
        )
        if step is None:
            raise ValueError("Correction target does not belong to this task")
        return step

    def _require_agent(self, workspace_id: UUID, agent_id: UUID) -> AgentProfile:
        agent = self._session.scalar(
            select(AgentProfile).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.id == agent_id,
            )
        )
        if agent is None:
            raise ValueError("Correction target does not belong to this workspace")
        return agent

    def _require_artifact(self, task: Task, artifact_id: UUID) -> Artifact:
        artifact = self._session.scalar(
            select(Artifact).where(
                Artifact.workspace_id == task.workspace_id,
                Artifact.task_id == task.id,
                Artifact.id == artifact_id,
            )
        )
        if artifact is None:
            raise ValueError("Correction target does not belong to this task")
        return artifact

    def _next_step_order(self, task: Task) -> int:
        current = self._session.scalar(
            select(func.coalesce(func.max(TaskStep.order_index), 0)).where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
            )
        )
        return int(current or 0) + 1


def _step_title(request: TaskCorrectionRequest) -> str:
    labels = {
        "revise": "Revise work",
        "regenerate": "Regenerate work",
        "add_missing_work": "Add missing work",
        "replace_artifact": "Replace artifact",
    }
    return f"{labels.get(request.mode, 'Correct work')}: {request.target_type}"


def _expected_artifacts(request: TaskCorrectionRequest) -> list[str]:
    if request.mode == "replace_artifact":
        return ["replacement_artifact"]
    if request.target_type == "final_output":
        return ["final_delivery"]
    return ["correction_result"]


def _assigned_agent_profile_id(task: Task, target_payload: dict[str, object]) -> UUID | None:
    raw_profile_id = target_payload.get("agent_profile_id")
    if isinstance(raw_profile_id, str):
        try:
            return UUID(raw_profile_id)
        except ValueError:
            pass
    return task.owner_agent_profile_id
