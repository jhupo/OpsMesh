from __future__ import annotations

from backend.app.agents.models import AgentProfile
from backend.app.runtime_spaces.models import RuntimeSpace
from backend.app.security.redaction import (
    redact_sensitive_payload_item,
    redact_text_fragments,
)
from backend.app.teams.models import AgentTeam, AgentTeamMember
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
        "team_name": redact_text_fragments(team.name),
        "team_type": team.team_type,
        "description": redact_text_fragments(team.description),
        "status": team.status,
        "manager_agent_profile_id": team.manager_agent_profile_id,
        "runtime_space_id": team.runtime_space_id,
        "runtime_space": _runtime_space_payload(runtime_space),
        "coordination_rules": redact_sensitive_payload_item(
            team.coordination_rules or {}, text_mode="fragments"
        ),
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
        "name": redact_text_fragments(runtime_space.name),
        "scope": runtime_space.scope,
        "status": runtime_space.status,
        "default_runtime_template_id": runtime_space.default_runtime_template_id,
        "policy": redact_sensitive_payload_item(runtime_space.policy, text_mode="fragments"),
        "network_policy": redact_sensitive_payload_item(
            runtime_space.network_policy, text_mode="fragments"
        ),
        "storage_policy": redact_sensitive_payload_item(
            runtime_space.storage_policy, text_mode="fragments"
        ),
        "cleanup_policy": redact_sensitive_payload_item(
            runtime_space.cleanup_policy, text_mode="fragments"
        ),
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
        "max_concurrent_tasks": sum(member.max_concurrent_tasks for member in accepting_members),
        "roles": sorted({member.team_role for member in active_members}),
        "departments": sorted(
            {member.department for member in active_members if member.department is not None}
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
        "agent_name": redact_text_fragments(agent.name),
        "agent_role": agent.role,
        "team_role": member.team_role,
        "department": redact_sensitive_payload_item(member.department, text_mode="fragments"),
        "position_title": redact_sensitive_payload_item(
            member.position_title, text_mode="fragments"
        ),
        "reports_to_member_id": member.reports_to_member_id,
        "accepts_tasks": member.accepts_tasks,
        "is_required": member.is_required,
        "max_concurrent_tasks": member.max_concurrent_tasks,
        "order_index": member.order_index,
        "agent_policy": _agent_policy(agent),
    }


def _agent_policy(agent: AgentProfile) -> dict[str, object]:
    return {
        "model": redact_text_fragments(agent.model),
        "model_settings": redact_sensitive_payload_item(
            agent.model_settings, text_mode="fragments"
        ),
        "tool_policy": redact_sensitive_payload_item(agent.tool_policy, text_mode="fragments"),
        "runtime_preferences": redact_sensitive_payload_item(
            agent.runtime_policy, text_mode="fragments"
        ),
        "runtime_policy": redact_sensitive_payload_item(
            agent.runtime_policy, text_mode="fragments"
        ),
        "approval_policy": redact_sensitive_payload_item(
            agent.approval_policy, text_mode="fragments"
        ),
        "memory_policy": redact_sensitive_payload_item(agent.memory_policy, text_mode="fragments"),
        "capabilities": redact_sensitive_payload_item(agent.capabilities, text_mode="fragments"),
        "skills": redact_sensitive_payload_item(agent.skills, text_mode="fragments"),
    }


def _visible_task_policy(value: object) -> dict[str, object]:
    return visible_task_policy(value, redact=True)
