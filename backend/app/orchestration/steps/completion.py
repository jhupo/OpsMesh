import json
from collections.abc import Callable
from contextlib import suppress
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.capabilities.schema_validation import validate_json_value
from backend.app.orchestration.planning.pm_acceptance import PmAcceptanceService
from backend.app.orchestration.planning.step_payload import step_message_payload
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.tasks.message_append import TaskMessageAppendService
from backend.app.tasks.models import TaskMessage, TaskStep
from backend.app.tasks.step_service import TaskStepStateService
from backend.app.tasks.step_status import TaskStepStatus

AppendEvent = Callable[[AgentRun, str, str, dict[str, object] | None], RunEvent]


class TaskStepCompletionService:
    def __init__(self, session: Session, append_event: AppendEvent) -> None:
        self._session = session
        self._append_event = append_event

    def mark_step_completed(self, run: AgentRun, final_output: str) -> None:
        if run.task_step_id is None:
            return
        step = self._session.get(TaskStep, run.task_step_id)
        if step is None or step.workspace_id != run.workspace_id:
            return
        result_payload = result_payload_for_step(run, final_output)
        TaskStepStateService().transition(
            step,
            TaskStepStatus.COMPLETED,
            result_payload=result_payload,
            result_summary=PmAcceptanceService(self._session).step_result_summary(
                step,
                final_output,
            ),
        )
        self._append_event(run, "task_step.completed", step.title, None)
        self.append_task_message(
            task_id=step.task_id,
            workspace_id=step.workspace_id,
            message_type="step.completed",
            body=step.result_summary or step.title,
            task_step_id=step.id,
            agent_run_id=run.id,
            agent_profile_id=run.agent_profile_id,
            payload={
                **step_message_payload(step),
                "result_summary": step.result_summary,
            },
        )
        if step.work_package_id == "manager-planning":
            self.append_manager_planning_completed_message(step, run)

    def validate_step_output(self, run: AgentRun, final_output: str) -> None:
        if run.task_step_id is None:
            return
        step = self._session.get(TaskStep, run.task_step_id)
        if step is None or step.workspace_id != run.workspace_id:
            return
        dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
        schema = dependencies.get("output_schema")
        if not isinstance(schema, dict):
            return
        payload = result_payload_for_step(run, final_output)
        value = payload.get("structured_output", payload.get("final_output"))
        if isinstance(value, dict) and "value" in value:
            value = value["value"]
        elif isinstance(value, str):
            with suppress(json.JSONDecodeError):
                value = json.loads(value)
        validate_json_value(value, schema, label="workflow step output")

    def append_manager_planning_completed_message(
        self,
        step: TaskStep,
        run: AgentRun,
    ) -> None:
        if self.task_message_exists(
            step.workspace_id,
            step.task_id,
            message_type="planning.completed",
        ):
            return
        self.append_task_message(
            task_id=step.task_id,
            workspace_id=step.workspace_id,
            message_type="planning.completed",
            body="Project plan generated.",
            task_step_id=step.id,
            agent_run_id=run.id,
            agent_profile_id=run.agent_profile_id,
            payload={
                **step_message_payload(step),
                "source": "manager_planning_step",
                "result_summary": step.result_summary,
            },
        )

    def task_message_exists(
        self,
        workspace_id: UUID,
        task_id: UUID,
        *,
        message_type: str,
    ) -> bool:
        return (
            self._session.scalar(
                select(TaskMessage.id).where(
                    TaskMessage.workspace_id == workspace_id,
                    TaskMessage.task_id == task_id,
                    TaskMessage.message_type == message_type,
                )
            )
            is not None
        )

    def append_task_message(
        self,
        *,
        task_id: UUID,
        workspace_id: UUID,
        message_type: str,
        body: str,
        task_step_id: UUID | None = None,
        agent_run_id: UUID | None = None,
        agent_profile_id: UUID | None = None,
        payload: dict[str, object] | None = None,
    ) -> TaskMessage:
        return TaskMessageAppendService(self._session).append(
            workspace_id=workspace_id,
            task_id=task_id,
            task_step_id=task_step_id,
            agent_run_id=agent_run_id,
            agent_profile_id=agent_profile_id,
            message_type=message_type,
            body=body,
            payload=payload or {},
        )


def result_payload_for_step(run: AgentRun, final_output: str) -> dict[str, object]:
    payload = dict(run.output) if isinstance(run.output, dict) else {}
    payload.setdefault("final_output", final_output)
    return payload
