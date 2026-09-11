from backend.app.tasks.models import TaskStep


def step_message_payload(step: TaskStep) -> dict[str, object]:
    return {
        "work_package_id": step.work_package_id,
        "required_role": step.required_role,
        "required_skills": step.required_skills,
        "expected_artifacts": step.expected_artifacts,
        "acceptance_criteria": step.acceptance_criteria,
        "review_policy": step.review_policy,
    }
