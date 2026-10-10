from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from opsmesh.orchestration.definitions.graph import is_pm_summary_step
from opsmesh.orchestration.planning.acceptance_contract import PmAcceptanceResult
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.tasks.models import Task, TaskMessage, TaskStep
from opsmesh.shared.utils import (
    dict_list,
    string_list,
    string_or_default,
)

AppendTaskMessage = Callable[..., TaskMessage]


@dataclass(slots=True)
class PmAcceptanceService:
    session: Session

    def step_result_summary(self, step: TaskStep, run: AgentRun, final_output: str) -> str:
        if not is_pm_summary_step(step):
            return final_output
        pm_acceptance = self.acceptance_from_output(step, run)
        summary = pm_acceptance.get("summary")
        return str(summary) if isinstance(summary, str) and summary else final_output

    def acceptance_for_completed_run(
        self,
        run: AgentRun,
    ) -> dict[str, object] | None:
        if run.task_step_id is None:
            return None
        step = self.session.get(TaskStep, run.task_step_id)
        if step is None or step.workspace_id != run.workspace_id:
            return None
        if not is_pm_summary_step(step):
            return None
        return self.acceptance_from_output(step, run)

    def acceptance_from_output(
        self,
        step: TaskStep,
        run: AgentRun,
    ) -> dict[str, object]:
        value = self.validated_acceptance(run.output or {})
        return {
            **PmAcceptanceResult.model_validate(value).model_dump(),
            "review_policy": step.review_policy,
            "raw_output": value,
        }

    @staticmethod
    def validated_acceptance(output: dict[str, object]) -> dict[str, object]:
        structured = output.get("structured_output")
        if (
            not isinstance(structured, dict)
            or structured.get("validated") is not True
            or structured.get("schema_name") != "pm_acceptance"
        ):
            raise ValueError("PM acceptance requires the SDK-validated pm_acceptance output")
        value = structured.get("value")
        if not isinstance(value, dict):
            raise ValueError("PM acceptance output must be an object")
        return PmAcceptanceResult.model_validate(value).model_dump(exclude_unset=True)

    def append_decision_message(
        self,
        task: Task,
        *,
        run: AgentRun,
        pm_acceptance: dict[str, object],
        append_task_message: AppendTaskMessage,
    ) -> None:
        if run.task_step_id is None:
            return
        decision = str(pm_acceptance.get("decision") or "approved")
        summary = string_or_default(pm_acceptance.get("summary"), decision)
        append_task_message(
            task_id=task.id,
            workspace_id=task.workspace_id,
            message_type="pm.acceptance_decision",
            body=summary,
            task_step_id=run.task_step_id,
            agent_run_id=run.id,
            agent_profile_id=run.agent_profile_id,
            payload={
                "decision": decision,
                "reasons": string_list(pm_acceptance.get("reasons")),
                "revision_requests": dict_list(pm_acceptance.get("revision_requests")),
                "missing_work_packages": dict_list(pm_acceptance.get("missing_work_packages")),
            },
        )
