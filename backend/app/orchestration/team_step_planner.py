from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.orchestration.run_eligibility import RunEligibilityService
from backend.app.orchestration.team_step_project_plan import ProjectPlanStepMaterializer
from backend.app.planning.agent_plan import is_agent_planning_step
from backend.app.tasks.models import Task, TaskStep


@dataclass(slots=True)
class TeamStepPlanner:
    session: Session

    def create_team_step_plan(self, task: Task) -> TaskStep | None:
        if task.agent_team_id is None:
            return None
        if (task.project_plan or {}).get("strategy") == "agent_planning_pending":
            steps = list(
                self.session.scalars(
                    select(TaskStep).where(
                        TaskStep.workspace_id == task.workspace_id,
                        TaskStep.task_id == task.id,
                    )
                )
            )
            attempt_id = str((task.project_plan or {})["plan_id"])
            for step in steps:
                if (
                    is_agent_planning_step(step)
                    and step.review_policy.get("attempt_id") == attempt_id
                ):
                    return step if step.status == "queued" else None
            if any(
                not is_agent_planning_step(step) or step.status not in {"failed", "cancelled"}
                for step in steps
            ):
                raise ValueError("Existing task work requires explicit replanning")
            assert task.project_plan is not None
            return ProjectPlanStepMaterializer(self.session).materialize(task, task.project_plan)
        existing = self.session.scalar(
            select(TaskStep.id)
            .where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
            )
            .limit(1)
        )
        if existing is not None:
            steps = RunEligibilityService(self.session).next_eligible_steps(
                task.id, task.workspace_id
            )
            return steps[0] if steps else None
        if task.project_plan is None:
            raise ValueError("Team work requires an admitted plan or queued planner attempt")
        return ProjectPlanStepMaterializer(self.session).materialize(task, task.project_plan)
