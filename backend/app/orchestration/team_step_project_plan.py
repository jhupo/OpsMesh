from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.core.typing import dict_or_empty, optional_string, string_list, uuid_or_none
from backend.app.tasks.models import Task, TaskStep

STEP_STATUS_QUEUED = "queued"


@dataclass(slots=True)
class ProjectPlanStepMaterializer:
    session: Session

    def materialize(self, task: Task, project_plan: dict[str, object]) -> TaskStep | None:
        raw_packages = project_plan.get("work_packages", [])
        if not isinstance(raw_packages, list):
            return None

        created_steps_by_package_id: dict[str, TaskStep] = {}
        first_step: TaskStep | None = None
        for index, package in enumerate(raw_packages):
            if not isinstance(package, dict):
                continue
            package_id = str(package.get("package_id") or f"package-{index}")
            after_step_ids = after_step_ids_for_package(package, created_steps_by_package_id)
            step = TaskStep(
                workspace_id=task.workspace_id,
                task_id=task.id,
                runtime_space_id=task.runtime_space_id,
                assigned_agent_profile_id=uuid_or_none(
                    package.get("assigned_agent_profile_id"),
                ),
                work_package_id=package_id,
                required_role=optional_string(package.get("required_role")),
                required_skills=string_list(package.get("required_skills")),
                expected_artifacts=string_list(package.get("expected_artifacts")),
                acceptance_criteria=string_list(package.get("acceptance_criteria")),
                review_policy=dict_or_empty(package.get("review_policy")),
                title=str(package.get("title") or "Work package"),
                description=str(package.get("description") or ""),
                status=STEP_STATUS_QUEUED,
                order_index=index * 100,
                dependencies={
                    "after_step_ids": after_step_ids,
                    "work_package_id": package_id,
                    "required_role": package.get("required_role"),
                    "required_skills": package.get("required_skills", []),
                    "expected_artifacts": package.get("expected_artifacts", []),
                    "acceptance_criteria": package.get("acceptance_criteria", []),
                    "review_policy": package.get("review_policy", {}),
                },
            )
            self.session.add(step)
            self.session.flush([step])
            created_steps_by_package_id[package_id] = step
            if first_step is None and not after_step_ids:
                first_step = step

        self.session.flush()
        return first_step


def after_step_ids_for_package(
    package: dict[str, object],
    created_steps_by_package_id: dict[str, TaskStep],
) -> list[str]:
    raw_dependencies = package.get("depends_on", [])
    dependencies = (
        [str(dependency) for dependency in raw_dependencies if isinstance(dependency, str)]
        if isinstance(raw_dependencies, list)
        else []
    )
    return [
        str(created_steps_by_package_id[dependency].id)
        for dependency in dependencies
        if dependency in created_steps_by_package_id
    ]
