"""Atomically admit a worker's structured plan before any downstream step can run."""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRunResult
from backend.app.planning.agent_plan import AgentPlanProposal, is_agent_planning_step
from backend.app.planning.attempts import TaskPlanningAttemptService
from backend.app.planning.models import TaskPlanningAttempt
from backend.app.planning.project_plan_validation import (
    ProjectPlanValidationError,
    validate_project_plan,
)
from backend.app.runs.models import AgentRun
from backend.app.tasks.message_append import TaskMessageAppendService
from backend.app.tasks.models import Task, TaskStep

from .team_step_project_plan import ProjectPlanStepMaterializer


class PlannerCompletionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def apply(self, run: AgentRun, result: AgentRunResult) -> bool:
        step = self.session.scalar(
            select(TaskStep).where(
                TaskStep.workspace_id == run.workspace_id,
                TaskStep.task_id == run.task_id,
                TaskStep.id == run.task_step_id,
            )
        )
        if not is_agent_planning_step(step):
            return True
        assert step is not None
        task = self.session.scalar(
            select(Task)
            .where(
                Task.workspace_id == run.workspace_id,
                Task.id == run.task_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if task is None:
            raise ProjectPlanValidationError("Planner task unavailable")
        attempt = self.session.scalar(
            select(TaskPlanningAttempt)
            .where(
                TaskPlanningAttempt.workspace_id == run.workspace_id,
                TaskPlanningAttempt.task_id == task.id,
                TaskPlanningAttempt.id == UUID(str(step.review_policy["attempt_id"])),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if attempt is None or attempt.planner_agent_profile_id != run.agent_profile_id:
            raise ProjectPlanValidationError("Planner attempt does not match run")
        if (task.project_plan or {}).get("plan_id") != str(attempt.id):
            raise ProjectPlanValidationError("Planner result is stale")
        if attempt.status == "completed":
            if (attempt.output_snapshot or {}).get("planner_run_id") != str(run.id):
                raise ProjectPlanValidationError("Planner attempt already consumed")
            return True
        if attempt.status not in {"queued", "running"}:
            return False
        try:
            if result.structured_output is None or not result.structured_output.validated:
                raise ProjectPlanValidationError("Planner requires SDK structured output")
            if (result.structured_output.schema_name, result.structured_output.schema_version) != (
                "task_plan",
                "1",
            ):
                raise ProjectPlanValidationError("Planner output schema does not match contract")
            proposal = AgentPlanProposal.model_validate(result.structured_output.value)
            packages = [item.model_dump(mode="json") for item in proposal.work_packages]
            if any(
                item["package_id"] in {"manager-planning", "manager-summary"} for item in packages
            ):
                raise ProjectPlanValidationError("Reserved planner package ID")
            for package in packages:
                package["review_policy"] = {"reviewer": "manager", "mode": "manager_review"}
            packages.append(
                {
                    "package_id": "manager-summary",
                    "title": "Manager summary",
                    "description": "Verify acceptance criteria and integrate every work package.",
                    "required_role": "project_manager",
                    "required_skills": [],
                    "assigned_agent_profile_id": str(run.agent_profile_id),
                    "depends_on": [item.package_id for item in proposal.work_packages],
                    "expected_artifacts": [],
                    "acceptance_criteria": ["All delivery criteria met."],
                    "review_policy": {"reviewer": "user", "mode": "final_acceptance"},
                }
            )
            plan: dict[str, object] = {
                "plan_version": 1,
                "plan_id": str(attempt.id),
                "objective": proposal.objective,
                "planner_agent_profile_id": str(run.agent_profile_id),
                "planner_run_id": str(run.id),
                "generated_at": datetime.now(UTC).isoformat(),
                "strategy": "agent_sdk",
                "work_packages": packages,
            }
            validate_project_plan(plan, task.team_snapshot)
        except (ValidationError, ProjectPlanValidationError) as exc:
            TaskPlanningAttemptService(self.session).reject_agent_plan(
                task,
                attempt,
                code=exc.code
                if isinstance(exc, ProjectPlanValidationError)
                else "plan_schema_invalid",
            )
            return False
        ProjectPlanStepMaterializer(self.session).materialize(task, plan)
        task.project_plan = plan
        attempt.status = "completed"
        attempt.output_snapshot = plan
        attempt.completed_at = datetime.now(UTC)
        TaskMessageAppendService(self.session).append_for_task(
            task,
            message_type="planning.completed",
            body="Agent plan validated and admitted.",
            payload={
                "attempt_id": str(attempt.id),
                "planner_run_id": str(run.id),
                "work_package_count": len(packages),
                "strategy": "agent_sdk",
            },
        )
        self.session.flush()
        return True
