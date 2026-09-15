from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.utils import (
    dict_or_empty,
    optional_string,
    string_list,
    uuid_or_none,
)
from backend.app.domains.orchestration.runs.eligibility import RunEligibilityService
from backend.app.domains.orchestration.tasks.models import Task, TaskStep
from backend.app.domains.orchestration.workflows.definitions.graph import (
    validate_project_plan,
)
from backend.app.domains.orchestration.workflows.planning.agent_plan import is_agent_planning_step

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
        "locked": package.get("locked", False),
        "required_tools": package.get("required_tools", []),
        "required_mcp_tools": package.get("required_mcp_tools", []),
        "required_resource_ids": package.get("required_resource_ids", []),
        "resource_requirements": package.get("resource_requirements", {}),
        "estimated_cost_usd": package.get("estimated_cost_usd", 0),
        "node_type": package.get("node_type", "agent"),
        "tool_name": package.get("tool_name"),
        "arguments": package.get("arguments", {}),
        "output_schema": package.get("output_schema"),
        "input_bindings": package.get("input_bindings", {}),
        "subworkflow_definition_id": package.get("subworkflow_definition_id"),
        "subworkflow_version": package.get("subworkflow_version"),
    }


@dataclass(slots=True)
class TeamStepPlanner:
    session: Session

    def create_team_step_plan(self, task: Task) -> TaskStep | None:
        if task.agent_team_id is None:
            return None
        if (task.project_plan or {}).get("strategy") == "agent_planning_pending":
            steps = list(
                self.session.scalars(
                    select(TaskStep).where(
                        TaskStep.workspace_id == task.workspace_id,
                        TaskStep.task_id == task.id,
                    )
                )
            )
            attempt_id = str((task.project_plan or {})["plan_id"])
            for step in steps:
                if (
                    is_agent_planning_step(step)
                    and step.review_policy.get("attempt_id") == attempt_id
                ):
                    return step if step.status == "queued" else None
            if any(
                not is_agent_planning_step(step) or step.status not in {"failed", "cancelled"}
                for step in steps
            ):
                raise ValueError("Existing task work requires explicit replanning")
            assert task.project_plan is not None
            ProjectPlanStepMaterializer(self.session).materialize(task, task.project_plan)
            eligible = RunEligibilityService(self.session).next_eligible_steps(
                task.id,
                task.workspace_id,
            )
            return eligible[0] if eligible else None
        existing = self.session.scalar(
            select(TaskStep.id)
            .where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
            )
            .limit(1)
        )
        if existing is not None:
            steps = RunEligibilityService(self.session).next_eligible_steps(
                task.id, task.workspace_id
            )
            return steps[0] if steps else None
        if task.project_plan is None:
            raise ValueError("Team work requires an admitted plan or queued planner attempt")
        ProjectPlanStepMaterializer(self.session).materialize(task, task.project_plan)
        eligible = RunEligibilityService(self.session).next_eligible_steps(
            task.id,
            task.workspace_id,
        )
        return eligible[0] if eligible else None
