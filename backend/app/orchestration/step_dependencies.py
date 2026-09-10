"""One fail-closed dependency predicate for all task schedulers."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.tasks.models import TaskStep


def dependencies_satisfied(session: Session, step: TaskStep) -> bool:
    if not isinstance(step.dependencies, dict):
        return False
    raw_ids = step.dependencies.get("after_step_ids", [])
    if not isinstance(raw_ids, list):
        return False
    if not raw_ids:
        return True
    try:
        ids = [UUID(str(value)) for value in raw_ids]
    except (ValueError, TypeError):
        return False
    if len(set(ids)) != len(ids) or step.id in ids:
        return False
    completed_count = session.scalar(
        select(func.count(TaskStep.id)).where(
            TaskStep.workspace_id == step.workspace_id,
            TaskStep.task_id == step.task_id,
            TaskStep.id.in_(ids),
            TaskStep.status == "completed",
        )
    )
    # Counting missing/incomplete rows as zero would incorrectly authorize missing or foreign IDs.
    return completed_count == len(ids)
