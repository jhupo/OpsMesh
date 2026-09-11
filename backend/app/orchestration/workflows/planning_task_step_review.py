from backend.app.tasks.models import TaskStep


def is_pm_summary_step(step: TaskStep) -> bool:
    review_policy = step.review_policy if isinstance(step.review_policy, dict) else {}
    return (
        step.work_package_id == "manager-summary"
        or review_policy.get("mode") == "final_acceptance"
    )
