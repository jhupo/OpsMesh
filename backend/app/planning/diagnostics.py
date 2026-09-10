from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.typing import (
    dict_list,
    dict_or_empty,
    string_list,
    string_or_default,
    uuid_or_none,
)
from backend.app.orchestration.conditions import condition_step_references
from backend.app.planning.member_matching import MemberMatchingService
from backend.app.planning.org_structure import build_org_structure
from backend.app.planning.project_plan_members import snapshot_agent_ids
from backend.app.runs.models import AgentRun
from backend.app.security.redaction import redact_sensitive_text
from backend.app.tasks.models import Task, TaskStep


@dataclass(frozen=True)
class _PackageNode:
    package_id: str
    depends_on: tuple[str, ...]


class ProjectPlanDiagnosticsService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._matcher = MemberMatchingService(session)

    def get_diagnostics(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
    ) -> dict[str, object] | None:
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            return None
        plan = task.project_plan if isinstance(task.project_plan, dict) else None
        snapshot = task.team_snapshot if isinstance(task.team_snapshot, dict) else None
        if plan is None:
            return {
                "workspace_id": workspace_id,
                "task_id": task.id,
                "plan_present": False,
                "plan_id": None,
                "strategy": None,
                "summary": {
                    "total_packages": 0,
                    "assigned_packages": 0,
                    "unassigned_packages": 0,
                    "unknown_dependencies": 0,
                    "cycle_packages": 0,
                },
                "manager": _manager_diagnostics(None, snapshot),
                "org_health": _org_health(snapshot, []),
                "packages": [],
                "dependency_graph": _dependency_graph([]),
                "blocked_reasons": ["no_project_plan"],
            }

        raw_packages = plan.get("work_packages")
        packages = (
            [item for item in raw_packages if isinstance(item, dict)]
            if isinstance(
                raw_packages,
                list,
            )
            else []
        )
        package_diagnostics = [
            self._package_diagnostics(package, snapshot, workspace_id) for package in packages
        ]
        steps = {
            str(step.work_package_id): step
            for step in self._session.scalars(
                select(TaskStep).where(
                    TaskStep.workspace_id == workspace_id,
                    TaskStep.task_id == task_id,
                )
            )
            if step.work_package_id is not None
        }
        attempts: dict[UUID | None, int] = {
            step_id: int(count)
            for step_id, count in self._session.execute(
                select(AgentRun.task_step_id, func.count(AgentRun.id))
                .where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.task_id == task_id,
                )
                .group_by(AgentRun.task_step_id)
            ).all()
        }
        for item in package_diagnostics:
            package_id = item["package_id"]
            step = steps.get(package_id) if isinstance(package_id, str) else None
            item.update(
                {
                    "task_step_id": step.id if step else None,
                    "execution_status": step.status if step else None,
                    "result_summary": redact_sensitive_text(step.result_summary)[:4000]
                    if step and step.result_summary
                    else None,
                    "attempt_count": attempts.get(step.id, 0) if step else 0,
                }
            )
        graph = _dependency_graph(_package_nodes(packages))
        managed_plan = plan.get("strategy") != "user_authored"
        org_health = _org_health(snapshot, packages, require_management=managed_plan)
        blocked_reasons = _plan_blocked_reasons(
            package_diagnostics=package_diagnostics,
            graph=graph,
            manager=_manager_diagnostics(plan, snapshot),
            org_health=org_health,
            require_management=managed_plan,
        )
        return {
            "workspace_id": workspace_id,
            "task_id": task.id,
            "plan_present": True,
            "plan_id": plan.get("plan_id") if isinstance(plan.get("plan_id"), str) else None,
            "strategy": plan.get("strategy") if isinstance(plan.get("strategy"), str) else None,
            "summary": {
                "total_packages": len(package_diagnostics),
                "assigned_packages": sum(
                    1
                    for package in package_diagnostics
                    if package["assignment_status"] == "assigned"
                ),
                "unassigned_packages": sum(
                    1
                    for package in package_diagnostics
                    if package["assignment_status"] == "unassigned"
                ),
                "unknown_dependencies": len(graph["unknown_dependencies"]),
                "cycle_packages": len(graph["cycle_package_ids"]),
            },
            "manager": _manager_diagnostics(plan, snapshot),
            "org_health": org_health,
            "packages": package_diagnostics,
            "dependency_graph": graph,
            "blocked_reasons": blocked_reasons,
        }

    def _package_diagnostics(
        self,
        package: dict[str, object],
        snapshot: dict[str, object] | None,
        workspace_id: UUID,
    ) -> dict[str, object]:
        package_id = _string_or_none(package.get("package_id"))
        assigned_agent_profile_id = uuid_or_none(package.get("assigned_agent_profile_id"))
        required_role = string_or_default(package.get("required_role"), "")
        required_skills = string_list(package.get("required_skills"))
        allowed_agent_ids = snapshot_agent_ids(snapshot or {})
        assignment_status = "unassigned"
        if assigned_agent_profile_id is not None:
            assignment_status = (
                "assigned"
                if str(assigned_agent_profile_id) in allowed_agent_ids
                else "agent_not_in_team_snapshot"
            )
        matches = (
            self._matcher.rank(
                team_snapshot=snapshot,
                required_role=required_role,
                required_skills=required_skills,
                workspace_id=workspace_id,
            )[:3]
            if isinstance(snapshot, dict)
            else []
        )
        return {
            "package_id": package_id,
            "title": string_or_default(package.get("title"), ""),
            "required_role": required_role,
            "required_skills": required_skills,
            "assigned_agent_profile_id": assigned_agent_profile_id,
            "assignment_status": assignment_status,
            "depends_on": string_list(package.get("depends_on")),
            "condition_dependencies": sorted(condition_step_references(package.get("condition"))),
            "join_policy": package.get("join_policy", "all_success"),
            "locked": package.get("locked", False),
            "expected_artifacts": string_list(package.get("expected_artifacts")),
            "acceptance_criteria": string_list(package.get("acceptance_criteria")),
            "review_policy": package.get("review_policy")
            if isinstance(package.get("review_policy"), dict)
            else {},
            "recommended_matches": [
                {
                    "agent_profile_id": match.agent_profile_id,
                    "team_member_id": match.team_member_id,
                    "team_role": match.team_role,
                    "score": round(match.score, 4),
                    "reasons": list(match.reasons),
                    "current_load": match.current_load,
                    "max_concurrent_tasks": match.max_concurrent_tasks,
                }
                for match in matches
            ],
        }


def _manager_diagnostics(
    plan: dict[str, object] | None,
    snapshot: dict[str, object] | None,
) -> dict[str, object]:
    team = dict_or_empty(dict_or_empty(snapshot).get("team"))
    manager_agent_profile_id = uuid_or_none(team.get("manager_agent_profile_id"))
    packages = dict_list(dict_or_empty(plan).get("work_packages"))
    package_ids = {
        package.get("package_id")
        for package in packages
        if isinstance(package.get("package_id"), str)
    }
    return {
        "manager_agent_profile_id": manager_agent_profile_id,
        "has_manager": manager_agent_profile_id is not None,
        "has_manager_planning": "manager-planning" in package_ids,
        "has_manager_summary": "manager-summary" in package_ids,
    }


def _dependency_graph(nodes: list[_PackageNode]) -> dict[str, list[str]]:
    package_ids = {node.package_id for node in nodes}
    unknown_dependencies = sorted(
        {
            dependency
            for node in nodes
            for dependency in node.depends_on
            if dependency not in package_ids
        }
    )
    cycle_package_ids = sorted(_cycle_package_ids(nodes))
    depended_on = {dependency for node in nodes for dependency in node.depends_on}
    roots = sorted(node.package_id for node in nodes if not node.depends_on)
    leaves = sorted(package_id for package_id in package_ids if package_id not in depended_on)
    return {
        "roots": roots,
        "leaves": leaves,
        "unknown_dependencies": unknown_dependencies,
        "cycle_package_ids": cycle_package_ids,
    }


def _cycle_package_ids(nodes: list[_PackageNode]) -> set[str]:
    dependencies = {node.package_id: node.depends_on for node in nodes}
    visiting: set[str] = set()
    visited: set[str] = set()
    cycles: set[str] = set()

    def visit(package_id: str, stack: list[str]) -> None:
        if package_id in visiting:
            if package_id in stack:
                cycles.update(stack[stack.index(package_id) :])
            return
        if package_id in visited:
            return
        visiting.add(package_id)
        for dependency in dependencies.get(package_id, ()):
            if dependency in dependencies:
                visit(dependency, [*stack, dependency])
        visiting.remove(package_id)
        visited.add(package_id)

    for node in nodes:
        visit(node.package_id, [node.package_id])
    return cycles


def _plan_blocked_reasons(
    *,
    package_diagnostics: list[dict[str, object]],
    graph: dict[str, list[str]],
    manager: dict[str, object],
    org_health: dict[str, object],
    require_management: bool = True,
) -> list[str]:
    reasons: list[str] = []
    if not package_diagnostics:
        reasons.append("no_work_packages")
    if any(package["assignment_status"] == "unassigned" for package in package_diagnostics):
        reasons.append("unassigned_work_packages")
    if any(
        package["assignment_status"] == "agent_not_in_team_snapshot"
        for package in package_diagnostics
    ):
        reasons.append("assigned_agent_not_in_team_snapshot")
    if graph["unknown_dependencies"]:
        reasons.append("unknown_dependencies")
    if graph["cycle_package_ids"]:
        reasons.append("dependency_cycle")
    if require_management and manager["has_manager"] and not manager["has_manager_planning"]:
        reasons.append("missing_manager_planning")
    if require_management and manager["has_manager"] and not manager["has_manager_summary"]:
        reasons.append("missing_manager_summary")
    reasons.extend(string_list(org_health.get("blocked_reasons")))
    return reasons


def _org_health(
    snapshot: dict[str, object] | None,
    packages: list[dict[str, object]],
    *,
    require_management: bool = True,
) -> dict[str, object]:
    org = build_org_structure(snapshot)
    package_ids = {
        str(package.get("package_id"))
        for package in packages
        if isinstance(package.get("package_id"), str)
    }
    blocked_reasons: list[str] = []
    warnings: list[str] = []
    if not org.executives:
        warnings.append("missing_executive_role")
    if require_management and not org.managers:
        blocked_reasons.append("missing_project_manager_role")
    if org.contributors and not org.leads:
        warnings.append("missing_team_lead_role")
    if org.cycle_member_ids:
        blocked_reasons.append("reporting_cycle")
    if org.orphan_member_ids:
        warnings.append("orphan_reporting_members")
    if (
        require_management
        and org.leads
        and not any("lead-review" in package_id for package_id in package_ids)
    ):
        blocked_reasons.append("missing_lead_review")
    return {
        "status": "blocked" if blocked_reasons else "warning" if warnings else "healthy",
        "executive_count": len(org.executives),
        "manager_count": len(org.managers),
        "lead_count": len(org.leads),
        "contributor_count": len(org.contributors),
        "department_count": len(org.departments),
        "cycle_member_ids": list(org.cycle_member_ids),
        "orphan_member_ids": list(org.orphan_member_ids),
        "blocked_reasons": blocked_reasons,
        "warnings": warnings,
    }


def _package_nodes(packages: list[dict[str, object]]) -> list[_PackageNode]:
    nodes: list[_PackageNode] = []
    for package in packages:
        package_id = _string_or_none(package.get("package_id"))
        if package_id is None:
            continue
        nodes.append(
            _PackageNode(
                package_id=package_id,
                depends_on=tuple(
                    sorted(
                        set(string_list(package.get("depends_on")))
                        | condition_step_references(package.get("condition"))
                    )
                ),
            )
        )
    return nodes


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
