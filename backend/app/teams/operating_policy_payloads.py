from __future__ import annotations

from backend.app.runtime_spaces.models import RuntimeSpace
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.teams.team_context_redaction import (
    redact_context_value,
    redact_secret_like_text,
)
from backend.app.teams.team_policy_visibility import visible_task_policy


def operating_policy_payload(
    *,
    team: AgentTeam,
    members: list[AgentTeamMember],
    runtime_space: RuntimeSpace | None,
) -> dict[str, object]:
    active_members = [member for member in members if member.status == "active"]
    accepting_members = [member for member in active_members if member.accepts_tasks]

    return {
        "workspace_id": team.workspace_id,
        "team_id": team.id,
        "team_name": redact_secret_like_text(team.name),
        "team_type": team.team_type,
        "description": redact_secret_like_text(team.description),
        "status": team.status,
        "manager_agent_profile_id": team.manager_agent_profile_id,
        "runtime_space_id": team.runtime_space_id,
        "runtime_space": _runtime_space_payload(runtime_space),
        "coordination_rules": redact_context_value(team.coordination_rules or {}),
        "default_task_policy": _visible_task_policy(team.default_task_policy),
        "staffing": _staffing_payload(
            members=members,
            active_members=active_members,
            accepting_members=accepting_members,
        ),
        "members": [_member_policy_summary(member) for member in active_members],
    }


def _runtime_space_payload(runtime_space: RuntimeSpace | None) -> dict[str, object] | None:
    if runtime_space is None:
        return None
    return {
        "id": runtime_space.id,
        "name": redact_secret_like_text(runtime_space.name),
        "scope": runtime_space.scope,
        "status": runtime_space.status,
        "default_runtime_template_id": runtime_space.default_runtime_template_id,
        "policy": redact_context_value(runtime_space.policy),
        "network_policy": redact_context_value(runtime_space.network_policy),
        "storage_policy": redact_context_value(runtime_space.storage_policy),
        "cleanup_policy": redact_context_value(runtime_space.cleanup_policy),
    }


def _staffing_payload(
    *,
    members: list[AgentTeamMember],
    active_members: list[AgentTeamMember],
    accepting_members: list[AgentTeamMember],
) -> dict[str, object]:
    return {
        "total_member_count": len(members),
        "active_member_count": len(active_members),
        "accepting_member_count": len(accepting_members),
        "required_member_count": sum(1 for member in active_members if member.is_required),
        "inactive_member_count": sum(1 for member in members if member.status != "active"),
        "non_accepting_member_count": len(active_members) - len(accepting_members),
        "max_concurrent_tasks": sum(
            member.max_concurrent_tasks for member in accepting_members
        ),
        "roles": sorted({member.team_role for member in active_members}),
        "departments": sorted(
            {
                member.department
                for member in active_members
                if member.department is not None
            }
        ),
        "member_limits": [_member_limits(member) for member in members],
    }


def _member_limits(member: AgentTeamMember) -> dict[str, object]:
    return {
        "member_id": member.id,
        "agent_profile_id": member.agent_profile_id,
        "team_role": member.team_role,
        "status": member.status,
        "accepts_tasks": member.accepts_tasks,
        "is_required": member.is_required,
        "max_concurrent_tasks": member.max_concurrent_tasks,
    }


def _member_policy_summary(member: AgentTeamMember) -> dict[str, object]:
    agent = member.agent_profile
    return {
        "member_id": member.id,
        "agent_profile_id": member.agent_profile_id,
        "agent_name": redact_secret_like_text(agent.name),
        "agent_role": agent.role,
        "team_role": member.team_role,
        "department": redact_context_value(member.department),
        "position_title": redact_context_value(member.position_title),
        "reports_to_member_id": member.reports_to_member_id,
        "accepts_tasks": member.accepts_tasks,
        "is_required": member.is_required,
        "max_concurrent_tasks": member.max_concurrent_tasks,
        "order_index": member.order_index,
        "agent_policy": _agent_policy(agent),
    }


def _agent_policy(agent) -> dict[str, object]:
    return {
        "model": redact_secret_like_text(agent.model),
        "model_settings": redact_context_value(agent.model_settings),
        "tool_policy": redact_context_value(agent.tool_policy),
        "runtime_preferences": redact_context_value(
            getattr(agent, "runtime_preferences", agent.runtime_policy)
        ),
        "runtime_policy": redact_context_value(agent.runtime_policy),
        "approval_policy": redact_context_value(agent.approval_policy),
        "memory_policy": redact_context_value(agent.memory_policy),
        "capabilities": redact_context_value(agent.capabilities),
        "skills": redact_context_value(agent.skills),
    }


def _visible_task_policy(value: object) -> dict[str, object]:
    return visible_task_policy(value, redact=True)
