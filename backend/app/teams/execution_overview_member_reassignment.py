from __future__ import annotations

from uuid import UUID

from backend.app.agents.models import AgentProfile
from backend.app.tasks.models import TaskStep
from backend.app.teams.execution_overview_constants import (
    REASSIGNABLE_SPECIALIST_STEP_STATUSES,
)
from backend.app.teams.execution_overview_utils import string_list
from backend.app.teams.models import AgentTeamMember


def specialist_reassignments(
    members: list[AgentTeamMember],
    agents: dict[UUID, AgentProfile],
    steps: list[TaskStep],
) -> list[dict[str, object]]:
    reassignments: list[dict[str, object]] = []
    for step in steps:
        if not is_reassignable_specialist_step(step):
            continue
        replacement = replacement_member_for_step(step=step, members=members, agents=agents)
        if replacement is None:
            continue
        member, agent = replacement
        reassignments.append(_reassignment_payload(step=step, member=member, agent=agent))
    return reassignments


def is_reassignable_specialist_step(step: TaskStep) -> bool:
    if step.status not in REASSIGNABLE_SPECIALIST_STEP_STATUSES:
        return False
    if step.assigned_agent_profile_id is None:
        return False
    if is_manager_role(step.required_role):
        return False
    work_package_id = step.work_package_id or ""
    return not work_package_id.startswith("manager-")


def replacement_member_for_step(
    *,
    step: TaskStep,
    members: list[AgentTeamMember],
    agents: dict[UUID, AgentProfile],
) -> tuple[AgentTeamMember, AgentProfile] | None:
    candidates: list[tuple[int, int, str, AgentTeamMember, AgentProfile]] = []
    for member in members:
        candidate = _replacement_candidate(step=step, member=member, agents=agents)
        if candidate is not None:
            candidates.append(candidate)
    if not candidates:
        return None
    _, _, _, member, agent = sorted(candidates, key=lambda item: item[:3])[0]
    return member, agent


def member_matches_step(
    *,
    member: AgentTeamMember,
    agent: AgentProfile,
    step: TaskStep,
) -> bool:
    required_role = step.required_role
    if required_role is not None and required_role not in {member.team_role, agent.role}:
        return False
    required_skills = string_list(step.required_skills)
    if not required_skills:
        return True
    member_skills = {str(skill) for skill in member.skill_weights}
    agent_skills = {str(skill) for skill in agent.skills}
    return all(skill in member_skills or skill in agent_skills for skill in required_skills)


def replacement_role_rank(
    *,
    member: AgentTeamMember,
    agent: AgentProfile,
    step: TaskStep,
) -> int:
    required_role = step.required_role
    if required_role is not None and member.team_role == required_role:
        return 0
    if required_role is not None and agent.role == required_role:
        return 1
    return 2


def is_manager_role(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.lower().replace("-", "_")
    return any(
        marker in normalized
        for marker in ("manager", "project_manager", "program_manager", "product_manager")
    )


def _replacement_candidate(
    *,
    step: TaskStep,
    member: AgentTeamMember,
    agents: dict[UUID, AgentProfile],
) -> tuple[int, int, str, AgentTeamMember, AgentProfile] | None:
    if member.agent_profile_id == step.assigned_agent_profile_id:
        return None
    agent = agents.get(member.agent_profile_id)
    if agent is None or agent.workspace_id != step.workspace_id or agent.status != "active":
        return None
    if member.status != "active" or not member.accepts_tasks:
        return None
    if is_manager_role(member.team_role) or is_manager_role(agent.role):
        return None
    if not member_matches_step(member=member, agent=agent, step=step):
        return None
    return (
        replacement_role_rank(member=member, agent=agent, step=step),
        member.order_index,
        str(member.id),
        member,
        agent,
    )


def _reassignment_payload(
    *,
    step: TaskStep,
    member: AgentTeamMember,
    agent: AgentProfile,
) -> dict[str, object]:
    return {
        "task_id": step.task_id,
        "task_step_id": step.id,
        "step_status": step.status,
        "required_role": step.required_role,
        "required_skills": string_list(step.required_skills),
        "current_agent_profile_id": step.assigned_agent_profile_id,
        "replacement_agent_profile_id": member.agent_profile_id,
        "replacement_agent_name": agent.name,
        "replacement_agent_role": agent.role,
        "replacement_team_role": member.team_role,
        "reason": "blocked_or_failed_specialist_step",
    }
