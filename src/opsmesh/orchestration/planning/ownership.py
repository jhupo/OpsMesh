"""Protect authored execution graphs from automatic plan replacement."""

from opsmesh.orchestration.definitions.graph import ProjectPlanValidationError
from opsmesh.orchestration.tasks.models import Task


def require_automatic_plan_ownership(task: Task) -> None:
    if task.orchestration_definition_id is not None or (
        isinstance(task.project_plan, dict) and task.project_plan.get("strategy") == "user_authored"
    ):
        raise ProjectPlanValidationError(
            "User-authored workflows require explicit plan mutation, not automatic regeneration",
            code="plan_authored_graph_protected",
        )
