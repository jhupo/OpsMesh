from __future__ import annotations

from backend.app.planning.project_plan_members import member_agent_profile_id, member_role
from backend.app.planning.project_plan_models import ProjectWorkPackage
from backend.app.planning.project_plan_utils import (
    dict_or_default,
    expected_artifacts_for_role,
    skill_names,
    string_list,
    string_or_default,
    uuid_or_none,
)


def executive_alignment_package(
    *,
    package_id: str,
    role: str,
    agent_profile_id: object,
) -> ProjectWorkPackage:
    return ProjectWorkPackage(
        package_id=package_id,
        title=f"{role} alignment",
        description=(
            "Set executive intent, success criteria, constraints, and decision "
            "boundaries for the project."
        ),
        required_role=role,
        required_skills=("strategy", "alignment"),
        assigned_agent_profile_id=uuid_or_none(agent_profile_id),
        depends_on=(),
        expected_artifacts=("executive_alignment",),
        acceptance_criteria=(
            "Strategic goals and non-goals are clear.",
            "Decision constraints are ready for planning.",
        ),
        review_policy={"reviewer": "executive", "mode": "self_review"},
    )


def manager_planning_package(
    *,
    package_id: str,
    manager_planner_id: object,
    executive_alignment_ids: list[str],
) -> ProjectWorkPackage:
    return ProjectWorkPackage(
        package_id=package_id,
        title="Manager planning",
        description="Clarify the goal, split responsibilities, and prepare the team plan.",
        required_role="project_manager",
        required_skills=("planning", "coordination"),
        assigned_agent_profile_id=uuid_or_none(manager_planner_id),
        depends_on=tuple(executive_alignment_ids),
        expected_artifacts=("project_plan",),
        acceptance_criteria=("The team has a clear execution plan.",),
        review_policy={"reviewer": "manager", "mode": "self_review"},
    )


def lead_breakdown_package(
    *,
    package_id: str,
    lead: dict[str, object],
    department: str,
    planning_dependencies: tuple[str, ...],
    agent_profile_id: object,
) -> ProjectWorkPackage:
    return ProjectWorkPackage(
        package_id=package_id,
        title=f"{department} breakdown",
        description=(
            "Translate the project plan into department-level tasks, handoffs, "
            "risks, and quality checks."
        ),
        required_role=member_role(lead, "team_lead"),
        required_skills=tuple(skill_names(lead.get("skill_weights"))) or (
            "breakdown",
            "coordination",
        ),
        assigned_agent_profile_id=uuid_or_none(agent_profile_id),
        depends_on=planning_dependencies,
        expected_artifacts=("department_plan",),
        acceptance_criteria=(
            "Execution responsibilities are clear for the department.",
            "Dependencies and review checkpoints are identified.",
        ),
        review_policy={"reviewer": "manager", "mode": "manager_review"},
    )


def member_execution_package(
    *,
    package_id: str,
    member: dict[str, object],
    role: str,
    agent_profile_id: object,
    dependencies: tuple[str, ...],
) -> ProjectWorkPackage:
    return ProjectWorkPackage(
        package_id=package_id,
        title=f"{role} execution",
        description=f"Complete the assigned {role} work package for the task.",
        required_role=role,
        required_skills=tuple(skill_names(member.get("skill_weights"))),
        assigned_agent_profile_id=uuid_or_none(agent_profile_id),
        depends_on=dependencies,
        expected_artifacts=tuple(expected_artifacts_for_role(role)),
        acceptance_criteria=(
            "The work package produces a clear result summary.",
            "Any generated files or artifacts are attached to the task.",
        ),
        review_policy={"reviewer": "manager", "mode": "manager_review"},
    )


def requested_package(
    *,
    request: dict[str, object],
    package_id: str,
    role: str,
    required_skills: list[str],
    matched_member: dict[str, object] | None,
    dependencies: tuple[str, ...],
) -> ProjectWorkPackage:
    raw_mcp_tools = request.get("required_mcp_tools", [])
    raw_resource_ids = request.get("required_resource_ids", [])
    raw_estimated_cost = request.get("estimated_cost_usd")
    estimated_cost = (
        float(raw_estimated_cost)
        if isinstance(raw_estimated_cost, int | float)
        and not isinstance(raw_estimated_cost, bool)
        else 0.0
    )
    return ProjectWorkPackage(
        package_id=package_id,
        title=string_or_default(request.get("title"), f"{role} execution"),
        description=string_or_default(
            request.get("description"),
            f"Complete the assigned {role} work package for the task.",
        ),
        required_role=role,
        required_skills=tuple(required_skills),
        assigned_agent_profile_id=member_agent_profile_id(matched_member),
        depends_on=dependencies,
        expected_artifacts=tuple(
            string_list(request.get("expected_artifacts")) or expected_artifacts_for_role(role)
        ),
        acceptance_criteria=tuple(
            string_list(request.get("acceptance_criteria"))
            or ["The work package produces a clear result summary."]
        ),
        review_policy=dict_or_default(
            request.get("review_policy"),
            {"reviewer": "manager", "mode": "manager_review"},
        ),
        condition=dict_or_default(request.get("condition"), {}),
        required_tools=tuple(string_list(request.get("required_tools"))),
        required_mcp_tools=tuple(_dict_items(raw_mcp_tools)),
        required_resource_ids=tuple(
            resource_id
            for item in _list_items(raw_resource_ids)
            if (resource_id := uuid_or_none(item)) is not None
        ),
        resource_requirements={
            str(key): value
            for key, value in dict_or_default(request.get("resource_requirements"), {}).items()
            if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool)
        },
        estimated_cost_usd=estimated_cost,
    )


def _list_items(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _dict_items(value: object) -> list[dict[str, object]]:
    return [item for item in _list_items(value) if isinstance(item, dict)]


def lead_review_package(
    *,
    package_id: str,
    lead: dict[str, object],
    department: str,
    agent_profile_id: object,
    execution_ids: list[str],
    has_manager: bool,
    has_executives: bool,
) -> ProjectWorkPackage:
    return ProjectWorkPackage(
        package_id=package_id,
        title=f"{department} review",
        description="Review department execution, resolve defects, and summarize readiness.",
        required_role=member_role(lead, "team_lead"),
        required_skills=("review", "quality"),
        assigned_agent_profile_id=uuid_or_none(agent_profile_id),
        depends_on=tuple(execution_ids),
        expected_artifacts=("lead_review",),
        acceptance_criteria=(
            "Department deliverables meet the requested acceptance criteria.",
            "Open risks are documented for the manager or executive reviewer.",
        ),
        review_policy={
            "reviewer": "manager" if has_manager else "executive" if has_executives else "user",
            "mode": "manager_review" if has_manager else "final_acceptance",
        },
    )


def manager_summary_package(
    *,
    package_id: str,
    manager_planner_id: object,
    dependencies: list[str],
    has_executives: bool,
) -> ProjectWorkPackage:
    return ProjectWorkPackage(
        package_id=package_id,
        title="Manager summary",
        description="Review team outputs, reconcile issues, and prepare the delivery summary.",
        required_role="project_manager",
        required_skills=("review", "synthesis"),
        assigned_agent_profile_id=uuid_or_none(manager_planner_id),
        depends_on=tuple(dependencies),
        expected_artifacts=("final_delivery",),
        acceptance_criteria=("The final answer integrates all completed work packages.",),
        review_policy={
            "reviewer": "executive" if has_executives else "user",
            "mode": "executive_review" if has_executives else "final_acceptance",
        },
    )


def executive_approval_package(
    *,
    package_id: str,
    role: str,
    agent_profile_id: object,
    dependencies: tuple[str, ...],
) -> ProjectWorkPackage:
    return ProjectWorkPackage(
        package_id=package_id,
        title=f"{role} approval",
        description=(
            "Review the final management summary against executive intent and approve the delivery."
        ),
        required_role=role,
        required_skills=("approval", "strategy"),
        assigned_agent_profile_id=uuid_or_none(agent_profile_id),
        depends_on=dependencies,
        expected_artifacts=("executive_approval",),
        acceptance_criteria=("The delivery satisfies executive goals and constraints.",),
        review_policy={"reviewer": "user", "mode": "final_acceptance"},
    )
