from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.model_providers.model_api import configured_model_api
from backend.app.planning.org_structure import build_org_structure
from backend.app.teams.models import AgentTeam, AgentTeamMember


def build_team_snapshot(
    session: Session,
    *,
    workspace_id: UUID,
    team_id: UUID,
) -> dict[str, object]:
    team = session.scalar(
        select(AgentTeam).where(
            AgentTeam.workspace_id == workspace_id,
            AgentTeam.id == team_id,
            AgentTeam.status == "active",
        )
    )
    if team is None:
        raise ValueError("Team not found")

    members = session.scalars(
        select(AgentTeamMember)
        .where(
            AgentTeamMember.workspace_id == workspace_id,
            AgentTeamMember.agent_team_id == team_id,
            AgentTeamMember.status == "active",
        )
        .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.id.asc())
    ).all()
    agent_ids = {
        member.agent_profile_id for member in members if member.agent_profile_id is not None
    }
    if team.manager_agent_profile_id is not None:
        agent_ids.add(team.manager_agent_profile_id)

    agents = {
        agent.id: agent
        for agent in session.scalars(
            select(AgentProfile).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.id.in_(agent_ids),
            )
        ).all()
    }

    snapshot = {
        "snapshot_version": 2,
        "captured_at": datetime.now(UTC).isoformat(),
        "team": {
            "id": str(team.id),
            "name": team.name,
            "team_type": team.team_type,
            "description": team.description,
            "manager_agent_profile_id": _str_or_none(team.manager_agent_profile_id),
            "coordination_rules": team.coordination_rules,
            "default_task_policy": team.default_task_policy,
            "capability_policy": team.capability_policy,
            "capability_policy_version": team.capability_policy_version,
            "status": team.status,
        },
        "members": [
            _member_snapshot(member, agents.get(member.agent_profile_id))
            for member in members
            if member.accepts_tasks
        ],
        "agents": [
            _agent_snapshot(agent)
            for agent in sorted(agents.values(), key=lambda item: item.name.lower())
        ],
    }
    snapshot["organization"] = _organization_snapshot(snapshot)
    return snapshot


def _member_snapshot(
    member: AgentTeamMember,
    agent: AgentProfile | None,
) -> dict[str, object]:
    return {
        "id": str(member.id),
        "agent_profile_id": str(member.agent_profile_id),
        "reports_to_member_id": _str_or_none(member.reports_to_member_id),
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
        "agent": _agent_snapshot(agent) if agent is not None else None,
    }


def _agent_snapshot(agent: AgentProfile | None) -> dict[str, object] | None:
    if agent is None:
        return None
    return {
        "id": str(agent.id),
        "name": agent.name,
        "role": agent.role,
        "description": agent.description,
        "instructions": agent.instructions,
        "model": agent.model,
        "model_provider_credential_id": _str_or_none(agent.model_provider_credential_id),
        "model_api": configured_model_api(agent.model_settings or {}),
        "capabilities": agent.capabilities,
        "skills": agent.skills,
        "tool_policy": agent.tool_policy,
        "runtime_policy": agent.runtime_policy,
        "memory_policy": agent.memory_policy,
        "approval_policy": agent.approval_policy,
        "version": agent.version,
        "status": agent.status,
    }


def _organization_snapshot(snapshot: dict[str, object]) -> dict[str, object]:
    org = build_org_structure(snapshot)
    return {
        "executive_member_ids": [member.id for member in org.executives],
        "manager_member_ids": [member.id for member in org.managers],
        "lead_member_ids": [member.id for member in org.leads],
        "contributor_member_ids": [member.id for member in org.contributors],
        "departments": {
            department: [member.id for member in members]
            for department, members in org.departments.items()
        },
        "reporting_tree": {
            member_id: [child.id for child in children]
            for member_id, children in org.children_by_member_id.items()
        },
        "orphan_member_ids": list(org.orphan_member_ids),
        "cycle_member_ids": list(org.cycle_member_ids),
        "role_counts": {
            "executives": len(org.executives),
            "managers": len(org.managers),
            "leads": len(org.leads),
            "contributors": len(org.contributors),
        },
    }


def _str_or_none(value: object | None) -> str | None:
    return str(value) if value is not None else None
