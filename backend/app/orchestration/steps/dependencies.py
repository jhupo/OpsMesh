"""One fail-closed dependency predicate for all task schedulers."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.tasks.models import TaskStep


def dependencies_satisfied(session: Session, step: TaskStep) -> bool:
    return dependency_decision(session, step) == "ready"


def dependency_decision(session: Session, step: TaskStep) -> str:
    if not isinstance(step.dependencies, dict):
        return "waiting"
    raw_ids = step.dependencies.get("after_step_ids", [])
    if not isinstance(raw_ids, list):
        return "waiting"
    if not raw_ids:
        return "ready"
    try:
        ids = [UUID(str(value)) for value in raw_ids]
    except (ValueError, TypeError):
        return "waiting"
    if len(set(ids)) != len(ids) or step.id in ids:
        return "waiting"
    statuses = list(
        session.scalars(
            select(TaskStep.status).where(
                TaskStep.workspace_id == step.workspace_id,
                TaskStep.task_id == step.task_id,
                TaskStep.id.in_(ids),
            )
        )
    )
    if len(statuses) != len(ids):
        return "waiting"
    if any(status not in {"completed", "skipped"} for status in statuses):
        return "waiting"
    policy = step.dependencies.get("join_policy", "all_success")
    if policy == "all_selected":
        return "ready" if "completed" in statuses else "skip"
    if policy == "all_success":
        return "skip" if "skipped" in statuses else "ready"
    return "waiting"
