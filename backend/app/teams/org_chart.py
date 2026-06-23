from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.teams.models import AgentTeam, AgentTeamMember


@dataclass(slots=True)
class TeamOrgChartBuilder:
    session: Session

    def build(self, workspace_id: UUID, team: AgentTeam) -> dict[str, object]:
        members = self._team_members(workspace_id, team.id)
        agents = self._agents_by_id(workspace_id, team, members)
        member_nodes = [
            _team_org_member_node(member, agents.get(member.agent_profile_id)) for member in members
        ]
        roots, orphan_member_ids, cycle_member_ids = _link_member_nodes(member_nodes, members)
        return {
            "workspace_id": team.workspace_id,
            "team_id": team.id,
            "name": team.name,
            "team_type": team.team_type,
            "description": team.description,
            "status": team.status,
            "manager_agent_profile_id": team.manager_agent_profile_id,
            "manager_agent": _team_org_agent_summary(agents.get(team.manager_agent_profile_id))
            if team.manager_agent_profile_id is not None
            else None,
            "runtime_space_id": team.runtime_space_id,
            "coordination_rules": team.coordination_rules,
            "default_task_policy": team.default_task_policy,
            "roots": roots,
            "members": member_nodes,
            "orphan_member_ids": orphan_member_ids,
            "cycle_member_ids": cycle_member_ids,
            "capacity_summary": _team_capacity_summary(members),
        }

    def _team_members(self, workspace_id: UUID, team_id: UUID) -> list[AgentTeamMember]:
        return list(
            self.session.scalars(
                select(AgentTeamMember)
                .where(
                    AgentTeamMember.workspace_id == workspace_id,
                    AgentTeamMember.agent_team_id == team_id,
                )
                .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.id.asc())
            )
        )

    def _agents_by_id(
        self,
        workspace_id: UUID,
        team: AgentTeam,
        members: list[AgentTeamMember],
    ) -> dict[UUID, AgentProfile]:
        agent_ids = {member.agent_profile_id for member in members}
        if team.manager_agent_profile_id is not None:
            agent_ids.add(team.manager_agent_profile_id)
        if not agent_ids:
            return {}
        return {
            agent.id: agent
            for agent in self.session.scalars(
                select(AgentProfile).where(
                    AgentProfile.workspace_id == workspace_id,
                    AgentProfile.id.in_(agent_ids),
                )
            )
        }


def _link_member_nodes(
    member_nodes: list[dict[str, object]],
    members: list[AgentTeamMember],
) -> tuple[list[dict[str, object]], list[UUID], list[UUID]]:
    node_by_id = {str(node["id"]): node for node in member_nodes}
    reports_to_by_id = {
        str(member.id): str(member.reports_to_member_id)
        for member in members
        if member.reports_to_member_id is not None
    }
    roots: list[dict[str, object]] = []
    orphan_member_ids: list[UUID] = []
    cycle_member_ids: list[UUID] = []
    for node in member_nodes:
        reports_to_member_id = node["reports_to_member_id"]
        parent = (
            node_by_id.get(str(reports_to_member_id))
            if reports_to_member_id is not None
            else None
        )
        if reports_to_member_id is not None and has_reporting_cycle(
            str(node["id"]),
            reports_to_by_id,
        ):
            roots.append(node)
            cycle_member_ids.append(node["id"])
            continue
        if parent is None:
            roots.append(node)
            if reports_to_member_id is not None:
                orphan_member_ids.append(node["id"])
            continue
        parent["children"].append(node)
    return roots, orphan_member_ids, cycle_member_ids


def has_reporting_cycle(member_id: str, reports_to_by_id: dict[str, str]) -> bool:
    seen: set[str] = set()
    current = member_id
    while current in reports_to_by_id:
        if current in seen:
            return True
        seen.add(current)
        current = reports_to_by_id[current]
    return False


def _team_org_member_node(
    member: AgentTeamMember,
    agent: AgentProfile | None,
) -> dict[str, object]:
    return {
        "id": member.id,
        "agent_profile_id": member.agent_profile_id,
        "reports_to_member_id": member.reports_to_member_id,
        "team_role": member.team_role,
        "department": member.department,
        "position_title": member.position_title,
        "responsibilities": member.responsibilities,
        "skill_weights": member.skill_weights,
        "availability": member.availability,
        "max_concurrent_tasks": member.max_concurrent_tasks,
        "accepts_tasks": member.accepts_tasks,
        "is_required": member.is_required,
        "order_index": member.order_index,
        "status": member.status,
        "agent": _team_org_agent_summary(agent),
        "children": [],
    }


def _team_org_agent_summary(agent: AgentProfile | None) -> dict[str, object] | None:
    if agent is None:
        return None
    return {
        "id": agent.id,
        "name": agent.name,
        "role": agent.role,
        "status": agent.status,
    }


def _team_capacity_summary(members: list[AgentTeamMember]) -> dict[str, int]:
    return {
        "total_members": len(members),
        "active_members": sum(1 for member in members if member.status == "active"),
        "accepting_members": sum(
            1 for member in members if member.status == "active" and member.accepts_tasks
        ),
        "required_members": sum(
            1 for member in members if member.status == "active" and member.is_required
        ),
        "inactive_members": sum(1 for member in members if member.status != "active"),
        "total_max_concurrent_tasks": sum(
            member.max_concurrent_tasks
            for member in members
            if member.status == "active" and member.accepts_tasks
        ),
    }
