"""Project a mutated work-package graph onto execution steps."""

from uuid import UUID

from opsmesh.orchestration.planning.team_project_plan import (
    ProjectPlanStepMaterializer,
    step_dependencies_for_package,
)
from opsmesh.orchestration.tasks.models import Task, TaskStep
from opsmesh.shared.utils import string_list, uuid_or_none


def build_step(
    materializer: ProjectPlanStepMaterializer,
    task: Task,
    package: dict[str, object],
    *,
    order_index: int,
) -> TaskStep:
    return materializer.build_step(task, package, order_index=order_index)


def project_step(
    step: TaskStep,
    package: dict[str, object],
    after_ids: list[str],
) -> None:
    step.assigned_agent_profile_id = _required_uuid(package.get("assigned_agent_profile_id"))
    step.required_role = str(package.get("required_role") or "")
    step.required_skills = string_list(package.get("required_skills"))
    step.expected_artifacts = string_list(package.get("expected_artifacts"))
    step.acceptance_criteria = string_list(package.get("acceptance_criteria"))
    review_policy = package.get("review_policy")
    step.review_policy = dict(review_policy) if isinstance(review_policy, dict) else {}
    step.title = str(package.get("title") or "Work package")
    step.description = str(package.get("description") or "")
    step.dependencies = step_dependencies_for_package(package, after_ids)


def _required_uuid(value: object) -> UUID:
    parsed = uuid_or_none(value)
    if parsed is None:
        raise ValueError("Agent profile ID is invalid")
    return parsed
