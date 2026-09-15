from __future__ import annotations

from backend.app.domains.orchestration.tasks.models import TaskStep
from backend.app.domains.orchestration.workflows.definitions.conditions import (
    ConditionValidationError,
    validate_condition,
)
from backend.app.domains.orchestration.workflows.definitions.graph import (
    WorkflowGraphError,
    validate_workflow_graph,
)
from backend.app.domains.orchestration.workflows.templates.members import (
    snapshot_agent_ids,
)


class ProjectPlanValidationError(ValueError):
    def __init__(self, message: str, *, code: str = "plan_invalid") -> None:
        super().__init__(message)
        self.code = code


def is_pm_summary_step(step: TaskStep) -> bool:
    review_policy = step.review_policy if isinstance(step.review_policy, dict) else {}
    return (
        step.work_package_id == "manager-summary"
        or review_policy.get("mode") == "final_acceptance"
    )


def validate_project_plan(
    plan: dict[str, object],
    team_snapshot: dict[str, object] | None,
) -> None:
    if not isinstance(team_snapshot, dict):
        raise ProjectPlanValidationError("Project plan requires a team snapshot")
    packages = plan.get("work_packages")
    if not isinstance(packages, list) or not packages:
        raise ProjectPlanValidationError("Project plan must include work packages")
    if len(packages) > 256:
        raise ProjectPlanValidationError("Plan exceeds 256 work packages", code="plan_too_large")

    allowed_agent_ids = snapshot_agent_ids(team_snapshot)
    package_ids = _validate_package_shape(packages, allowed_agent_ids)
    try:
        validate_workflow_graph(packages, package_ids)
    except WorkflowGraphError as exc:
        raise ProjectPlanValidationError(str(exc), code=exc.code) from exc


def _validate_package_shape(packages: list[object], allowed_agent_ids: set[str]) -> set[str]:
    package_ids: set[str] = set()
    for raw_package in packages:
        if not isinstance(raw_package, dict):
            raise ProjectPlanValidationError("Work package must be an object")
        package_id = required_string(raw_package, "package_id")
        if package_id in package_ids:
            raise ProjectPlanValidationError(f"Duplicate work package id: {package_id}")
        package_ids.add(package_id)
        required_string(raw_package, "title")
        required_string(raw_package, "required_role")
        node_type = raw_package.get("node_type", "agent")
        if node_type not in {
            "agent",
            "tool",
            "mcp",
            "condition",
            "join",
            "approval",
            "subworkflow",
            "start",
            "end",
        }:
            raise ProjectPlanValidationError(
                "Unknown workflow node type", code="plan_node_type_invalid"
            )

        condition = raw_package.get("condition")
        if condition not in (None, {}):
            try:
                validate_condition(condition, path=f"work_packages[{package_id}].condition")
            except ConditionValidationError as exc:
                raise ProjectPlanValidationError(str(exc), code=exc.code) from exc

        agent_id = raw_package.get("assigned_agent_profile_id")
        if agent_id is not None and str(agent_id) not in allowed_agent_ids:
            raise ProjectPlanValidationError("Work package assigned agent is not in team snapshot")
    return package_ids


def required_string(value: dict[str, object], key: str) -> str:
    raw_value = value.get(key)
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ProjectPlanValidationError(f"Work package missing required field: {key}")
    return raw_value
