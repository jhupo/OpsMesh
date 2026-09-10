"""Task ownership rules shared by transfer, scheduling, and run authorization."""

from uuid import UUID

from backend.app.tasks.models import Task, TaskStep


def is_platform_owned_step(step: TaskStep) -> bool:
    """Return whether the platform assigns this step to the task owner.

    Specialist work remains assigned by the admitted plan. Planning, integration,
    and final-acceptance steps are control-plane work and must follow ownership.
    """

    work_package_id = step.work_package_id or ""
    if work_package_id in {"manager-planning", "manager-summary"}:
        return True
    if work_package_id.startswith("manager-summary-revision-"):
        return True
    review_policy = step.review_policy if isinstance(step.review_policy, dict) else {}
    return review_policy.get("mode") in {"final_acceptance", "executive_review"}


def task_owner_can_execute_step(task: Task, step: TaskStep) -> bool:
    """Check the current task owner against a control-plane step assignment."""

    if task.agent_team_id is None or task.owner_agent_profile_id is None:
        return True
    if not is_platform_owned_step(step):
        return True
    return step.assigned_agent_profile_id == task.owner_agent_profile_id


def owner_version(task: Task) -> int:
    """Normalize rows created before ownership versioning was introduced."""

    return max(int(task.owner_version or 1), 1)


def uuid_text(value: UUID | None) -> str | None:
    return str(value) if value is not None else None
