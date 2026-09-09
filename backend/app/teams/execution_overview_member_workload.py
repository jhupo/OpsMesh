from __future__ import annotations

from collections import Counter, defaultdict
from uuid import UUID

from backend.app.agents.models import AgentProfile
from backend.app.runs.activity import run_activity
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.tasks.models import TaskStep
from backend.app.teams.execution_overview_constants import (
    ACTIVE_RUN_STATUSES,
    ACTIVE_STEP_STATUSES,
)
from backend.app.teams.execution_overview_contracts import MemberWorkload
from backend.app.teams.models import AgentTeamMember


def member_items(
    members: list[AgentTeamMember],
    agents: dict[UUID, AgentProfile],
    steps: list[TaskStep],
    runs: list[AgentRun],
    latest_events: dict[UUID, RunEvent],
    *,
    workspace_active_task_ids_by_agent: dict[UUID, set[UUID]],
) -> list[MemberWorkload]:
    active_steps_by_agent = _active_steps_by_agent(steps)
    active_runs_by_agent = _active_runs_by_agent(runs)

    return [
        member_item(
            member=member,
            agent=agents.get(member.agent_profile_id),
            active_steps=active_steps_by_agent.get(member.agent_profile_id, []),
            active_runs=active_runs_by_agent.get(member.agent_profile_id, []),
            latest_events=latest_events,
            workspace_active_task_ids=workspace_active_task_ids_by_agent.get(
                member.agent_profile_id,
                set(),
            ),
        )
        for member in members
    ]


def member_item(
    *,
    member: AgentTeamMember,
    agent: AgentProfile | None,
    active_steps: list[TaskStep],
    active_runs: list[AgentRun],
    latest_events: dict[UUID, RunEvent],
    workspace_active_task_ids: set[UUID],
) -> MemberWorkload:
    active_task_ids = {step.task_id for step in active_steps}
    utilization = (
        len(workspace_active_task_ids) / member.max_concurrent_tasks
        if member.max_concurrent_tasks > 0
        else 0.0
    )
    return {
        "team_member_id": member.id,
        "agent_profile_id": member.agent_profile_id,
        "reports_to_member_id": member.reports_to_member_id,
        "agent_name": agent.name if agent is not None else None,
        "agent_role": agent.role if agent is not None else None,
        "team_role": member.team_role,
        "department": member.department,
        "position_title": member.position_title,
        "responsibilities": list(member.responsibilities),
        "status": member.status,
        "accepts_tasks": member.accepts_tasks,
        "max_concurrent_tasks": member.max_concurrent_tasks,
        "active_task_count": len(active_task_ids),
        "workspace_active_task_count": len(workspace_active_task_ids),
        "workspace_active_task_ids": sorted(workspace_active_task_ids, key=str),
        "active_step_count": len(active_steps),
        "active_run_count": len(active_runs),
        "active_run_phase_counts": active_run_phase_counts(active_runs, latest_events),
        "utilization": round(utilization, 4),
        "overloaded": len(workspace_active_task_ids) > member.max_concurrent_tasks,
        "at_capacity": len(workspace_active_task_ids) >= member.max_concurrent_tasks,
        "blocked_reasons": member_blocked_reasons(
            member=member,
            agent=agent,
            active_task_count=len(workspace_active_task_ids),
        ),
    }


def member_blocked_reasons(
    *,
    member: AgentTeamMember,
    agent: AgentProfile | None,
    active_task_count: int,
) -> list[str]:
    reasons: list[str] = []
    if agent is None:
        reasons.append("missing_agent_profile")
    elif agent.status != "active":
        reasons.append("agent_inactive")
    if member.status != "active":
        reasons.append("member_inactive")
    if not member.accepts_tasks:
        reasons.append("member_not_accepting_tasks")
    if active_task_count > member.max_concurrent_tasks:
        reasons.append("member_over_capacity")
    elif active_task_count >= member.max_concurrent_tasks:
        reasons.append("member_at_capacity")
    return reasons


def active_run_phase_counts(
    runs: list[AgentRun],
    latest_events: dict[UUID, RunEvent],
) -> dict[str, int]:
    counts = Counter(
        str(run_activity(run, latest_events.get(run.id)).get("phase"))
        for run in runs
        if run.status in ACTIVE_RUN_STATUSES
    )
    return dict(sorted(counts.items()))


def _active_steps_by_agent(steps: list[TaskStep]) -> dict[UUID, list[TaskStep]]:
    grouped: dict[UUID, list[TaskStep]] = defaultdict(list)
    for step in steps:
        if step.assigned_agent_profile_id is None or step.status not in ACTIVE_STEP_STATUSES:
            continue
        grouped[step.assigned_agent_profile_id].append(step)
    return grouped


def _active_runs_by_agent(runs: list[AgentRun]) -> dict[UUID, list[AgentRun]]:
    grouped: dict[UUID, list[AgentRun]] = defaultdict(list)
    for run in runs:
        if run.agent_profile_id is None or run.status not in ACTIVE_RUN_STATUSES:
            continue
        grouped[run.agent_profile_id].append(run)
    return grouped
