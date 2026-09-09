from __future__ import annotations

from uuid import UUID

from backend.app.tasks.models import Task, TaskStep

BLOCKING_DEPENDENCY_KEYS = {
    "blocked_at",
    "blocked_reason",
    "blocked_resource_key",
    "blocked_resource_keys",
    "blocked_by",
    "scheduler",
}


def completed_source_steps(
    steps: list[TaskStep],
    task_step_ids: list[UUID],
) -> list[TaskStep]:
    requested_ids = set(task_step_ids)
    return [
        step
        for step in steps
        if step.status == "completed" and (not requested_ids or step.id in requested_ids)
    ]


def dependency_step_ids(dependencies: object) -> list[UUID]:
    if not isinstance(dependencies, dict):
        return []
    raw_values = dependencies.get("after_step_ids")
    if not isinstance(raw_values, list):
        return []
    step_ids: list[UUID] = []
    seen: set[UUID] = set()
    for raw_value in raw_values:
        try:
            step_id = UUID(str(raw_value))
        except (TypeError, ValueError):
            continue
        if step_id in seen:
            continue
        step_ids.append(step_id)
        seen.add(step_id)
    return step_ids


def without_blocking_keys(dependencies: object) -> dict[str, object]:
    if not isinstance(dependencies, dict):
        return {}
    return {
        key: value for key, value in dependencies.items() if key not in BLOCKING_DEPENDENCY_KEYS
    }


def manager_agent_id(task: Task) -> UUID | None:
    snapshot = task.team_snapshot if isinstance(task.team_snapshot, dict) else {}
    raw_team = snapshot.get("team")
    team = raw_team if isinstance(raw_team, dict) else {}
    manager_id = team.get("manager_agent_profile_id")
    if isinstance(manager_id, str):
        try:
            return UUID(manager_id)
        except ValueError:
            return None
    if isinstance(manager_id, UUID):
        return manager_id
    plan = task.project_plan if isinstance(task.project_plan, dict) else {}
    planner_id = plan.get("planner_agent_profile_id")
    if isinstance(planner_id, str):
        try:
            return UUID(planner_id)
        except ValueError:
            return None
    return planner_id if isinstance(planner_id, UUID) else None
