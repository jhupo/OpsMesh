from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.core.typing import dict_list, string_list, string_list_or_single, string_or_default
from backend.app.orchestration.runs.result_payloads import json_object_from_text
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task, TaskMessage, TaskStep

from .task_step_review import is_pm_summary_step

AppendTaskMessage = Callable[..., TaskMessage]


@dataclass(slots=True)
class PmAcceptanceService:
    session: Session

    def step_result_summary(self, step: TaskStep, final_output: str) -> str:
        if not is_pm_summary_step(step):
            return final_output
        pm_acceptance = self.acceptance_from_output(step, final_output)
        summary = pm_acceptance.get("summary")
        return str(summary) if isinstance(summary, str) and summary else final_output

    def acceptance_for_completed_run(
        self,
        run: AgentRun,
        final_output: str,
    ) -> dict[str, object] | None:
        if run.task_step_id is None:
            return None
        step = self.session.get(TaskStep, run.task_step_id)
        if step is None or step.workspace_id != run.workspace_id:
            return None
        if not is_pm_summary_step(step):
            return None
        return self.acceptance_from_output(step, final_output)

    def acceptance_from_output(
        self,
        step: TaskStep,
        final_output: str,
    ) -> dict[str, object]:
        raw_output = json_object_from_text(final_output)
        if raw_output is None:
            return {
                "decision": "request_revision",
                "summary": final_output,
                "reasons": ["pm_acceptance.invalid_json_requires_review"],
                "revision_requests": [],
                "missing_work_packages": [],
                "raw_output": final_output,
            }

        decision = normalize_pm_decision(raw_output.get("decision"))
        summary = raw_output.get("summary")
        if not isinstance(summary, str) or not summary:
            summary = raw_output.get("final_output")
        if not isinstance(summary, str) or not summary:
            summary = final_output
        reasons = string_list_or_single(raw_output.get("reasons") or raw_output.get("reason"))
        if decision is None:
            decision = "request_revision"
            reasons = [
                "pm_acceptance.invalid_decision_requires_review",
                *reasons,
            ]

        return {
            "decision": decision,
            "summary": summary,
            "reasons": reasons,
            "revision_requests": dict_list(raw_output.get("revision_requests")),
            "missing_work_packages": dict_list(raw_output.get("missing_work_packages")),
            "review_policy": step.review_policy,
            "raw_output": raw_output,
        }

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


def normalize_pm_decision(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"approved", "request_revision", "add_missing_work"}:
        return normalized
    return None
