from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.tasks.models import Task, TaskStep

STEP_STATUS_COMPLETED = "completed"


@dataclass(slots=True)
class PmFinalOutputService:
    session: Session

    def final_output_for_task(
        self,
        task: Task,
        *,
        fallback: dict[str, object] | None,
        pm_acceptance: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        completed_steps = self.session.scalars(
            select(TaskStep)
            .where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
                TaskStep.status == STEP_STATUS_COMPLETED,
            )
            .order_by(TaskStep.order_index.asc())
        ).all()
        if not completed_steps:
            return fallback

        final_summary = next(
            (
                step.result_summary
                for step in reversed(completed_steps)
                if step.result_summary is not None
            ),
            None,
        )
        if pm_acceptance is not None and isinstance(pm_acceptance.get("summary"), str):
            final_summary = str(pm_acceptance["summary"])
        return {
            "final_output": final_summary,
            "team_orchestration": {
                "steps": [
                    {
                        "task_step_id": str(step.id),
                        "title": step.title,
                        "status": step.status,
                        "work_package_id": step.work_package_id,
                        "required_role": step.required_role,
                        "agent_profile_id": str(step.assigned_agent_profile_id)
                        if step.assigned_agent_profile_id is not None
                        else None,
                        "result_summary": step.result_summary,
                    }
                    for step in completed_steps
                ],
            },
            "pm_acceptance": pm_acceptance,
        }
