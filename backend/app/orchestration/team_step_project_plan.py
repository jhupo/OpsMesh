from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.core.typing import dict_or_empty, optional_string, string_list, uuid_or_none
from backend.app.planning.project_plan_validation import validate_project_plan
from backend.app.tasks.models import Task, TaskStep

STEP_STATUS_QUEUED = "queued"


@dataclass(slots=True)
class ProjectPlanStepMaterializer:
    session: Session

    def materialize(self, task: Task, project_plan: dict[str, object]) -> TaskStep | None:
        validate_project_plan(project_plan, task.team_snapshot)
        raw_packages = project_plan.get("work_packages", [])
        if not isinstance(raw_packages, list):
            return None

        created_steps_by_package_id: dict[str, TaskStep] = {}
        first_step: TaskStep | None = None
        for index, package in enumerate(raw_packages):
            if not isinstance(package, dict):
                continue
            step = self.build_step(task, package, order_index=index * 100)
            self.session.add(step)
            self.session.flush([step])
            package_id = str(step.work_package_id)
            created_steps_by_package_id[package_id] = step
            if first_step is None and not package.get("depends_on"):
                first_step = step

        # Allocate every ID before resolving edges; model output need not be topologically ordered.
        for package in raw_packages:
            if not isinstance(package, dict):
                continue
            step = created_steps_by_package_id[str(package["package_id"])]
            step.dependencies = step_dependencies_for_package(
                package,
                after_step_ids_for_package(package, created_steps_by_package_id),
            )

        self.session.flush()
        return first_step

    def build_step(
        self,
        task: Task,
        package: dict[str, object],
        *,
        order_index: int,
    ) -> TaskStep:
        package_id = str(package.get("package_id") or "")
        return TaskStep(
            workspace_id=task.workspace_id,
            task_id=task.id,
            runtime_space_id=task.runtime_space_id,
            assigned_agent_profile_id=uuid_or_none(package.get("assigned_agent_profile_id")),
            work_package_id=package_id,
            required_role=optional_string(package.get("required_role")),
            required_skills=string_list(package.get("required_skills")),
            expected_artifacts=string_list(package.get("expected_artifacts")),
            acceptance_criteria=string_list(package.get("acceptance_criteria")),
            review_policy=dict_or_empty(package.get("review_policy")),
            title=str(package.get("title") or "Work package"),
            description=str(package.get("description") or ""),
            status=STEP_STATUS_QUEUED,
            order_index=order_index,
            dependencies=step_dependencies_for_package(package, []),
        )


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
    return [str(created_steps_by_package_id[dependency].id) for dependency in dependencies]


def step_dependencies_for_package(
    package: dict[str, object],
    after_step_ids: list[str],
) -> dict[str, object]:
    package_id = str(package.get("package_id") or "")
    return {
        "after_step_ids": after_step_ids,
        "work_package_id": package_id,
        "required_role": package.get("required_role"),
        "required_skills": package.get("required_skills", []),
        "expected_artifacts": package.get("expected_artifacts", []),
        "acceptance_criteria": package.get("acceptance_criteria", []),
        "review_policy": package.get("review_policy", {}),
        "condition": package.get("condition", {}),
        "join_policy": package.get("join_policy", "all_success"),
        "required_tools": package.get("required_tools", []),
        "required_mcp_tools": package.get("required_mcp_tools", []),
        "required_resource_ids": package.get("required_resource_ids", []),
        "resource_requirements": package.get("resource_requirements", {}),
        "estimated_cost_usd": package.get("estimated_cost_usd", 0),
    }
