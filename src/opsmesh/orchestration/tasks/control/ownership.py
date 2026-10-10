"""Task ownership rules shared by transfer, scheduling, and run authorization."""

from opsmesh.orchestration.tasks.models import Task, TaskStep


def is_platform_owned_step(step: TaskStep) -> bool:
    """Only an explicitly requested planning run follows the task entry agent."""
    review_policy = step.review_policy if isinstance(step.review_policy, dict) else {}
    return review_policy.get("mode") == "agent_planning"


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
