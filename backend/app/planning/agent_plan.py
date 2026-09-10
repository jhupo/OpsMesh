"""Product-owned planner output; provider SDKs own schema-constrained generation."""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.app.agent_runtime.contracts import AgentRuntimeOutputSchema
from backend.app.planning.project_plan_context import PlanningContext
from backend.app.planning.project_plan_models import ProjectPlan, ProjectWorkPackage
from backend.app.planning.project_plan_validation import ProjectPlanValidationError
from backend.app.planning.workflow_contracts import WorkflowNode
from backend.app.tasks.models import Task, TaskStep


class PlannedWork(WorkflowNode):
    """A proposed execution node must resolve its assigned agent."""

    assigned_agent_profile_id: UUID


class AgentPlanProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    objective: str = Field(min_length=1, max_length=4000)
    work_packages: list[PlannedWork] = Field(min_length=1, max_length=128)


def planner_output_schema() -> AgentRuntimeOutputSchema:
    return AgentRuntimeOutputSchema(
        name="task_plan", schema=AgentPlanProposal.model_json_schema(), version="2"
    )


def is_agent_planning_step(step: TaskStep | None) -> bool:
    return step is not None and (step.review_policy or {}).get("mode") == "agent_planning"


def planning_mode(task: Task) -> str:
    mode = (task.input or {}).get("planning_mode", "agent")
    if mode not in {"agent", "deterministic"}:
        raise ProjectPlanValidationError("Unsupported planning mode", code="planning_mode_invalid")
    return str(mode)


def bootstrap_plan(task: Task, attempt_id: UUID) -> dict[str, object]:
    context = PlanningContext.from_task(task)
    planner_id = context.planner_agent_profile_id if context is not None else None
    if planner_id is None:
        raise ProjectPlanValidationError("Planner requires an assigned team agent")
    return ProjectPlan(
        plan_id=str(attempt_id),
        objective=task.title,
        planner_agent_profile_id=planner_id,
        generated_at=datetime.now(UTC).isoformat(),
        strategy="agent_planning_pending",
        work_packages=(
            ProjectWorkPackage(
                package_id="manager-planning",
                title="Manager planning",
                description=(
                    "Generate a task-specific executable DAG using only the supplied team roster. "
                    "Propose work; do not execute it. The platform validates and schedules output. "
                    "Do not emit manager-planning or manager-summary: these are platform-owned."
                ),
                required_role="project_manager",
                required_skills=(),
                assigned_agent_profile_id=planner_id,
                depends_on=(),
                expected_artifacts=(),
                acceptance_criteria=("Produce a valid authorized task plan.",),
                review_policy={"mode": "agent_planning", "attempt_id": str(attempt_id)},
            ),
        ),
    ).as_dict()
