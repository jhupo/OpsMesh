from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.planning.member_matching import MemberMatchingService
from backend.app.tasks.models import Task


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
                "packages": [],
                "dependency_graph": _dependency_graph([]),
                "blocked_reasons": ["no_project_plan"],
            }

        raw_packages = plan.get("work_packages")
        packages = [item for item in raw_packages if isinstance(item, dict)] if isinstance(
            raw_packages,
            list,
        ) else []
        package_diagnostics = [
            self._package_diagnostics(package, snapshot, workspace_id)
            for package in packages
        ]
        graph = _dependency_graph(_package_nodes(packages))
        blocked_reasons = _plan_blocked_reasons(
            package_diagnostics=package_diagnostics,
            graph=graph,
            manager=_manager_diagnostics(plan, snapshot),
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
        assigned_agent_profile_id = _uuid_or_none(package.get("assigned_agent_profile_id"))
        required_role = _string_or_default(package.get("required_role"), "")
        required_skills = _string_list(package.get("required_skills"))
        allowed_agent_ids = _snapshot_agent_ids(snapshot)
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
            "title": _string_or_default(package.get("title"), ""),
            "required_role": required_role,
            "required_skills": required_skills,
            "assigned_agent_profile_id": assigned_agent_profile_id,
            "assignment_status": assignment_status,
            "depends_on": _string_list(package.get("depends_on")),
            "expected_artifacts": _string_list(package.get("expected_artifacts")),
            "acceptance_criteria": _string_list(package.get("acceptance_criteria")),
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
    manager_agent_profile_id = None
    if isinstance(snapshot, dict) and isinstance(snapshot.get("team"), dict):
        manager_agent_profile_id = _uuid_or_none(snapshot["team"].get("manager_agent_profile_id"))
    packages = []
    if isinstance(plan, dict) and isinstance(plan.get("work_packages"), list):
        packages = [item for item in plan["work_packages"] if isinstance(item, dict)]
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


def _dependency_graph(nodes: list[_PackageNode]) -> dict[str, object]:
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
    graph: dict[str, object],
    manager: dict[str, object],
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
    if manager["has_manager"] and not manager["has_manager_planning"]:
        reasons.append("missing_manager_planning")
    if manager["has_manager"] and not manager["has_manager_summary"]:
        reasons.append("missing_manager_summary")
    return reasons


def _package_nodes(packages: list[dict[str, object]]) -> list[_PackageNode]:
    nodes: list[_PackageNode] = []
    for package in packages:
        package_id = _string_or_none(package.get("package_id"))
        if package_id is None:
            continue
        nodes.append(
            _PackageNode(
                package_id=package_id,
                depends_on=tuple(_string_list(package.get("depends_on"))),
            )
        )
    return nodes


def _snapshot_agent_ids(snapshot: dict[str, object] | None) -> set[str]:
    if not isinstance(snapshot, dict):
        return set()
    ids: set[str] = set()
    team = snapshot.get("team")
    if isinstance(team, dict) and team.get("manager_agent_profile_id") is not None:
        ids.add(str(team["manager_agent_profile_id"]))
    members = snapshot.get("members")
    if isinstance(members, list):
        for member in members:
            if isinstance(member, dict) and member.get("agent_profile_id") is not None:
                ids.add(str(member["agent_profile_id"]))
    return ids


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_or_default(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _uuid_or_none(value: object | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None
