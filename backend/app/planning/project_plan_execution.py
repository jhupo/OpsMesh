from __future__ import annotations

from backend.app.planning.member_matching import MemberMatchingService
from backend.app.planning.project_plan_context import PlanningContext
from backend.app.planning.project_plan_members import (
    lead_package_for_member,
    lead_package_for_request,
    member_agent_profile_id,
    requested_member_match,
)
from backend.app.planning.project_plan_models import ProjectWorkPackage
from backend.app.planning.project_plan_packages import member_execution_package, requested_package
from backend.app.planning.project_plan_utils import (
    execution_dependencies,
    merge_dependencies,
    requested_work_packages,
    string_list,
    string_or_default,
    string_tuple,
    unique_package_id,
)
from backend.app.tasks.models import Task


class ExecutionPackageAppender:
    def __init__(self, matcher: MemberMatchingService) -> None:
        self._matcher = matcher

    def append(
        self,
        *,
        work_packages: list[ProjectWorkPackage],
        task: Task,
        context: PlanningContext,
        lead_package_ids: dict[str, str],
        manager_package_id: str | None,
        executive_alignment_ids: list[str],
    ) -> tuple[list[str], dict[str, list[str]]]:
        requested_packages = requested_work_packages(task.input)
        if requested_packages:
            return self._append_requested_packages(
                work_packages=work_packages,
                task=task,
                context=context,
                lead_package_ids=lead_package_ids,
                requested_packages=requested_packages,
                manager_package_id=manager_package_id,
                executive_alignment_ids=executive_alignment_ids,
            )
        return self._append_member_execution_packages(
            work_packages=work_packages,
            members=context.execution_members,
            leads=context.leads,
            lead_package_ids=lead_package_ids,
            manager_package_id=manager_package_id,
            executive_alignment_ids=executive_alignment_ids,
        )

    def _append_member_execution_packages(
        self,
        *,
        work_packages: list[ProjectWorkPackage],
        members: list[dict[str, object]],
        leads: list[dict[str, object]],
        lead_package_ids: dict[str, str],
        manager_package_id: str | None,
        executive_alignment_ids: list[str],
    ) -> tuple[list[str], dict[str, list[str]]]:
        execution_package_ids: list[str] = []
        execution_ids_by_lead: dict[str, list[str]] = {}
        existing_ids = {package.package_id for package in work_packages}
        for index, member in enumerate(members, start=1):
            package = self._member_execution_package(
                member=member,
                index=index,
                existing_ids=existing_ids,
                leads=leads,
                lead_package_ids=lead_package_ids,
                manager_package_id=manager_package_id,
                executive_alignment_ids=executive_alignment_ids,
            )
            if package is None:
                continue
            work_packages.append(package)
            execution_package_ids.append(package.package_id)
            lead_id = lead_package_for_member(
                member=member,
                leads=leads,
                lead_package_ids=lead_package_ids,
            )
            if lead_id is not None:
                execution_ids_by_lead.setdefault(lead_id, []).append(package.package_id)
        return execution_package_ids, execution_ids_by_lead

    def _member_execution_package(
        self,
        *,
        member: dict[str, object],
        index: int,
        existing_ids: set[str],
        leads: list[dict[str, object]],
        lead_package_ids: dict[str, str],
        manager_package_id: str | None,
        executive_alignment_ids: list[str],
    ) -> ProjectWorkPackage | None:
        agent_profile_id = member_agent_profile_id(member)
        if agent_profile_id is None:
            return None
        role = str(member.get("team_role") or "specialist")
        package_id = unique_package_id(f"{role}-{index}", existing_ids)
        existing_ids.add(package_id)
        lead_dependency_id = lead_package_for_member(
            member=member,
            leads=leads,
            lead_package_ids=lead_package_ids,
        )
        return member_execution_package(
            package_id=package_id,
            member=member,
            role=role,
            agent_profile_id=agent_profile_id,
            dependencies=execution_dependencies(
                lead_dependency_id=lead_dependency_id,
                manager_package_id=manager_package_id,
                executive_alignment_ids=executive_alignment_ids,
            ),
        )

    def _append_requested_packages(
        self,
        *,
        work_packages: list[ProjectWorkPackage],
        task: Task,
        context: PlanningContext,
        lead_package_ids: dict[str, str],
        requested_packages: list[dict[str, object]],
        manager_package_id: str | None,
        executive_alignment_ids: list[str],
    ) -> tuple[list[str], dict[str, list[str]]]:
        execution_package_ids: list[str] = []
        execution_ids_by_lead: dict[str, list[str]] = {}
        existing_ids = {package.package_id for package in work_packages}
        for index, request in enumerate(requested_packages, start=1):
            role = string_or_default(request.get("required_role"), "specialist")
            required_skills = string_list(request.get("required_skills"))
            matched_member = self._match_requested_member(
                task,
                context,
                role,
                required_skills,
                request,
            )
            requested_package_id = request.get("package_id")
            package_id = (
                requested_package_id
                if isinstance(requested_package_id, str) and requested_package_id
                else unique_package_id(f"{role}-{index}", existing_ids)
            )
            existing_ids.add(package_id)
            execution_package_ids.append(package_id)
            lead_dependency_id = lead_package_for_request(
                request=request,
                matched_member=matched_member,
                leads=context.leads,
                lead_package_ids=lead_package_ids,
            )
            if lead_dependency_id is not None:
                execution_ids_by_lead.setdefault(lead_dependency_id, []).append(package_id)
            work_packages.append(
                requested_package(
                    request=request,
                    package_id=package_id,
                    role=role,
                    required_skills=required_skills,
                    matched_member=matched_member,
                    dependencies=merge_dependencies(
                        string_tuple(request.get("depends_on")),
                        execution_dependencies(
                            lead_dependency_id=lead_dependency_id,
                            manager_package_id=manager_package_id,
                            executive_alignment_ids=executive_alignment_ids,
                        ),
                    ),
                )
            )
        return execution_package_ids, execution_ids_by_lead

    def _match_requested_member(
        self,
        task: Task,
        context: PlanningContext,
        role: str,
        required_skills: list[str],
        request: dict[str, object],
    ) -> dict[str, object] | None:
        match = self._matcher.match(
            team_snapshot=context.snapshot,
            required_role=role,
            required_skills=required_skills,
            workspace_id=task.workspace_id,
        )
        return requested_member_match(
            members=context.execution_members,
            role=role,
            required_skills=required_skills,
            request=request,
            matcher_agent_profile_id=match.agent_profile_id if match is not None else None,
        )
