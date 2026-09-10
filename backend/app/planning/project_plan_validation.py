from __future__ import annotations

from graphlib import CycleError, TopologicalSorter

from backend.app.planning.project_plan_members import snapshot_agent_ids


class ProjectPlanValidationError(ValueError):
    def __init__(self, message: str, *, code: str = "plan_invalid") -> None:
        super().__init__(message)
        self.code = code


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
    _validate_dependencies(packages, package_ids)


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

        agent_id = raw_package.get("assigned_agent_profile_id")
        if agent_id is not None and str(agent_id) not in allowed_agent_ids:
            raise ProjectPlanValidationError("Work package assigned agent is not in team snapshot")
    return package_ids


def _validate_dependencies(packages: list[object], package_ids: set[str]) -> None:
    graph: dict[str, list[str]] = {}
    for raw_package in packages:
        if not isinstance(raw_package, dict):
            continue
        depends_on = raw_package.get("depends_on", [])
        if not isinstance(depends_on, list):
            raise ProjectPlanValidationError("Work package dependencies must be a list")
        if any(not isinstance(item, str) or not item for item in depends_on):
            raise ProjectPlanValidationError("Dependency IDs must be nonempty strings")
        if len(set(depends_on)) != len(depends_on):
            raise ProjectPlanValidationError("Duplicate dependency", code="plan_duplicate_edge")
        for dependency in depends_on:
            if dependency not in package_ids:
                raise ProjectPlanValidationError(
                    "Unknown work package dependency", code="plan_missing_dependency"
                )
        graph[required_string(raw_package, "package_id")] = depends_on
    try:
        TopologicalSorter(graph).prepare()
    except CycleError as exc:
        raise ProjectPlanValidationError(
            "Work package dependency cycle", code="plan_dependency_cycle"
        ) from exc


def required_string(value: dict[str, object], key: str) -> str:
    raw_value = value.get(key)
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ProjectPlanValidationError(f"Work package missing required field: {key}")
    return raw_value
