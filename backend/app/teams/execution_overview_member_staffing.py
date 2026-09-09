from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from backend.app.agents.models import AgentProfile
from backend.app.tasks.models import TaskStep
from backend.app.teams.execution_overview_constants import ACTIVE_STEP_STATUSES
from backend.app.teams.execution_overview_contracts import StaffingGap
from backend.app.teams.execution_overview_utils import string_list
from backend.app.teams.models import AgentTeamMember


def staffing_gaps(
    members: list[AgentTeamMember],
    agents: dict[UUID, AgentProfile],
    steps: list[TaskStep],
) -> list[StaffingGap]:
    grouped_steps: dict[tuple[str | None, tuple[str, ...]], list[TaskStep]] = defaultdict(list)
    for step in steps:
        if not _is_unassigned_active_requirement(step):
            continue
        required_role = step.required_role or None
        required_skills = tuple(sorted(string_list(step.required_skills)))
        if required_role is None and not required_skills:
            continue
        if matching_member_count(
            members=members,
            agents=agents,
            required_role=required_role,
            required_skills=required_skills,
        ):
            continue
        grouped_steps[(required_role, required_skills)].append(step)
    return [_staffing_gap_payload(key, steps) for key, steps in _sorted_gaps(grouped_steps)]


def matching_member_count(
    *,
    members: list[AgentTeamMember],
    agents: dict[UUID, AgentProfile],
    required_role: str | None,
    required_skills: tuple[str, ...],
) -> int:
    return sum(
        1
        for member in members
        if _member_matches_requirement(
            member=member,
            agent=agents.get(member.agent_profile_id),
            required_role=required_role,
            required_skills=required_skills,
        )
    )


def _member_matches_requirement(
    *,
    member: AgentTeamMember,
    agent: AgentProfile | None,
    required_role: str | None,
    required_skills: tuple[str, ...],
) -> bool:
    if agent is None or agent.status != "active":
        return False
    if member.status != "active" or not member.accepts_tasks:
        return False
    if required_role is not None and required_role not in {member.team_role, agent.role}:
        return False
    member_skills = {str(skill) for skill in member.skill_weights}
    return not any(skill not in member_skills for skill in required_skills)


def _is_unassigned_active_requirement(step: TaskStep) -> bool:
    return step.status in ACTIVE_STEP_STATUSES and step.assigned_agent_profile_id is None


def _sorted_gaps(
    grouped_steps: dict[tuple[str | None, tuple[str, ...]], list[TaskStep]],
) -> list[tuple[tuple[str | None, tuple[str, ...]], list[TaskStep]]]:
    return sorted(
        grouped_steps.items(),
        key=lambda item: (
            item[0][0] or "",
            ",".join(item[0][1]),
            min(step.order_index for step in item[1]),
        ),
    )


def _staffing_gap_payload(
    key: tuple[str | None, tuple[str, ...]],
    gap_steps: list[TaskStep],
) -> StaffingGap:
    required_role, required_skills = key
    task_ids = sorted({step.task_id for step in gap_steps}, key=str)
    return {
        "required_role": required_role,
        "required_skills": list(required_skills),
        "step_count": len(gap_steps),
        "task_count": len(task_ids),
        "task_ids": task_ids,
        "task_step_ids": [step.id for step in gap_steps],
        "matching_member_count": 0,
        "recommended_action": "add_or_hire_team_member",
    }
