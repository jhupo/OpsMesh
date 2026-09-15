"""Workflow graph validation independent of persistence and API transport."""

from graphlib import CycleError, TopologicalSorter

from backend.app.domains.orchestration.workflows.definitions.conditions import (
    ConditionValidationError,
    condition_step_references,
)


class WorkflowGraphError(ValueError):
    def __init__(self, message: str, *, code: str = "plan_invalid") -> None:
        super().__init__(message)
        self.code = code


def validate_workflow_graph(packages: list[object], package_ids: set[str]) -> None:
    graph: dict[str, list[str]] = {}
    for raw_package in packages:
        if not isinstance(raw_package, dict):
            continue
        depends_on = raw_package.get("depends_on", [])
        if not isinstance(depends_on, list):
            raise WorkflowGraphError("Work package dependencies must be a list")
        if any(not isinstance(item, str) or not item for item in depends_on):
            raise WorkflowGraphError("Dependency IDs must be nonempty strings")
        if len(set(depends_on)) != len(depends_on):
            raise WorkflowGraphError("Duplicate dependency", code="plan_duplicate_edge")
        for dependency in depends_on:
            if dependency not in package_ids:
                raise WorkflowGraphError(
                    "Unknown work package dependency", code="plan_missing_dependency"
                )
        try:
            references = condition_step_references(raw_package.get("condition"))
        except ConditionValidationError as exc:
            raise WorkflowGraphError(str(exc), code=exc.code) from exc
        if references - package_ids:
            raise WorkflowGraphError(
                "Condition references unknown work", code="plan_condition_reference_invalid"
            )
        package_id = raw_package.get("package_id")
        if not isinstance(package_id, str) or not package_id:
            raise WorkflowGraphError("Work package missing required field: package_id")
        graph[package_id] = list(set(depends_on) | references)
    try:
        TopologicalSorter(graph).prepare()
    except CycleError as exc:
        raise WorkflowGraphError(
            "Work package dependency cycle", code="plan_dependency_cycle"
        ) from exc
