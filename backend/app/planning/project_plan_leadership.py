from __future__ import annotations

from backend.app.planning.project_plan_members import (
    member_agent_profile_id,
    member_department,
    member_key,
    member_role,
)
from backend.app.planning.project_plan_models import ProjectWorkPackage
from backend.app.planning.project_plan_packages import (
    executive_alignment_package,
    executive_approval_package,
    lead_breakdown_package,
    lead_review_package,
    manager_planning_package,
    manager_summary_package,
)
from backend.app.planning.project_plan_utils import slug, unique_package_id


class LeadershipPackageAppender:
    def append_executive_alignment(
        self,
        *,
        work_packages: list[ProjectWorkPackage],
        executives: list[dict[str, object]],
    ) -> list[str]:
        package_ids: list[str] = []
        for executive in executives:
            agent_profile_id = member_agent_profile_id(executive)
            if agent_profile_id is None:
                continue
            role = member_role(executive, "executive")
            package_id = unique_package_id(
                f"executive-alignment-{slug(role)}",
                {package.package_id for package in work_packages},
            )
            package_ids.append(package_id)
            work_packages.append(
                executive_alignment_package(
                    package_id=package_id,
                    role=role,
                    agent_profile_id=agent_profile_id,
                )
            )
        return package_ids

    def append_manager_planning(
        self,
        *,
        work_packages: list[ProjectWorkPackage],
        manager_planner_id: object,
        executive_alignment_ids: list[str],
    ) -> str | None:
        if manager_planner_id is None:
            return None
        package_id = "manager-planning"
        work_packages.append(
            manager_planning_package(
                package_id=package_id,
                manager_planner_id=manager_planner_id,
                executive_alignment_ids=executive_alignment_ids,
            )
        )
        return package_id

    def append_lead_breakdown(
        self,
        *,
        work_packages: list[ProjectWorkPackage],
        leads: list[dict[str, object]],
        manager_package_id: str | None,
        executive_alignment_ids: list[str],
    ) -> dict[str, str]:
        lead_package_ids: dict[str, str] = {}
        planning_dependencies = (
            (manager_package_id,)
            if manager_package_id is not None
            else tuple(executive_alignment_ids)
        )
        existing_ids = {package.package_id for package in work_packages}
        for lead in leads:
            agent_profile_id = member_agent_profile_id(lead)
            if agent_profile_id is None:
                continue
            department = member_department(lead) or member_role(lead, "lead")
            package_id = unique_package_id(f"lead-breakdown-{slug(department)}", existing_ids)
            existing_ids.add(package_id)
            lead_package_ids[member_key(lead)] = package_id
            work_packages.append(
                lead_breakdown_package(
                    package_id=package_id,
                    lead=lead,
                    department=department,
                    planning_dependencies=planning_dependencies,
                    agent_profile_id=agent_profile_id,
                )
            )
        return lead_package_ids

    def append_lead_review(
        self,
        *,
        work_packages: list[ProjectWorkPackage],
        leads: list[dict[str, object]],
        lead_package_ids: dict[str, str],
        execution_ids_by_lead: dict[str, list[str]],
        has_manager: bool,
        has_executives: bool,
    ) -> list[str]:
        review_ids: list[str] = []
        existing_ids = {package.package_id for package in work_packages}
        for lead in leads:
            lead_package_id = lead_package_ids.get(member_key(lead))
            if lead_package_id is None:
                continue
            execution_ids = execution_ids_by_lead.get(lead_package_id, [])
            if not execution_ids:
                continue
            agent_profile_id = member_agent_profile_id(lead)
            if agent_profile_id is None:
                continue
            department = member_department(lead) or member_role(lead, "lead")
            package_id = unique_package_id(f"lead-review-{slug(department)}", existing_ids)
            existing_ids.add(package_id)
            work_packages.append(
                lead_review_package(
                    package_id=package_id,
                    lead=lead,
                    department=department,
                    agent_profile_id=agent_profile_id,
                    execution_ids=execution_ids,
                    has_manager=has_manager,
                    has_executives=has_executives,
                )
            )
            review_ids.append(package_id)
        return review_ids

    def append_manager_summary(
        self,
        *,
        work_packages: list[ProjectWorkPackage],
        manager_planner_id: object,
        dependencies: list[str],
        has_executives: bool,
    ) -> str | None:
        if manager_planner_id is None or not dependencies:
            return None
        package_id = "manager-summary"
        work_packages.append(
            manager_summary_package(
                package_id=package_id,
                manager_planner_id=manager_planner_id,
                dependencies=dependencies,
                has_executives=has_executives,
            )
        )
        return package_id

    def append_executive_approval(
        self,
        *,
        work_packages: list[ProjectWorkPackage],
        executives: list[dict[str, object]],
        manager_summary_id: str | None,
        fallback_dependencies: list[str],
    ) -> None:
        if not executives:
            return
        dependencies = (
            (manager_summary_id,)
            if manager_summary_id is not None
            else tuple(fallback_dependencies)
        )
        if not dependencies:
            return
        existing_ids = {package.package_id for package in work_packages}
        for executive in executives:
            agent_profile_id = member_agent_profile_id(executive)
            if agent_profile_id is None:
                continue
            role = member_role(executive, "executive")
            package_id = unique_package_id(f"executive-approval-{slug(role)}", existing_ids)
            existing_ids.add(package_id)
            work_packages.append(
                executive_approval_package(
                    package_id=package_id,
                    role=role,
                    agent_profile_id=agent_profile_id,
                    dependencies=dependencies,
                )
            )
