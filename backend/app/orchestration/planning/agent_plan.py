"""Product-owned planner output; provider SDKs own schema-constrained generation."""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.agents.execution.contracts import AgentRuntimeOutputSchema
from backend.app.orchestration.definitions.contracts import WorkflowNode
from backend.app.orchestration.definitions.graph import ProjectPlanValidationError
from backend.app.orchestration.definitions.templates.builder import PlanningContext, ProjectPlan
from backend.app.orchestration.tasks.models import Task, TaskStep


class PlannedWork(WorkflowNode):
    """A proposed execution node must resolve its assigned agent."""

    assigned_agent_profile_id: UUID


class AgentPlanProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    objective: str = Field(min_length=1, max_length=4000)
    work_packages: list[PlannedWork] = Field(min_length=1, max_length=128)


def planner_output_schema() -> AgentRuntimeOutputSchema:
    return AgentRuntimeOutputSchema(
        # Workflow nodes contain dynamic tool arguments and policy maps. They cannot be
        # represented by strict schemas; output still passes JSON Schema and domain validation.
        name="task_plan",
        schema=AgentPlanProposal.model_json_schema(),
        version="3",
        strict=False,
    )


def is_agent_planning_step(step: TaskStep | None) -> bool:
    return step is not None and (step.review_policy or {}).get("mode") == "agent_planning"


def planning_mode(task: Task) -> str:
    mode = (task.input or {}).get("planning_mode", "direct")
    if mode not in {"agent", "explicit", "direct"}:
        raise ProjectPlanValidationError("Unsupported planning mode", code="planning_mode_invalid")
    return str(mode)


def bootstrap_plan(session: Session, task: Task, attempt_id: UUID) -> dict[str, object]:
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
            WorkflowNode(
                package_id="manager-planning",
                title="Manager planning",
                description=task.description or task.title,
                required_role=None,
                required_skills=(),
                assigned_agent_profile_id=planner_id,
                depends_on=(),
                expected_artifacts=(),
                acceptance_criteria=(),
                review_policy={"mode": "agent_planning", "attempt_id": str(attempt_id)},
            ),
        ),
    ).as_dict()
