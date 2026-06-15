from __future__ import annotations

from collections import Counter, defaultdict
from uuid import UUID

from backend.app.agents.models import AgentProfile
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task, TaskStep
from backend.app.teams.models import AgentTeam, AgentTeamMember


class WorkspaceEmployeeLoadBuilder:
    def __init__(
        self,
        *,
        teams: list[AgentTeam],
        members: list[AgentTeamMember],
        agents: dict[UUID, AgentProfile],
        tasks: list[Task],
        active_steps: list[TaskStep],
        active_runs: list[AgentRun],
    ) -> None:
        self._teams_by_id = {team.id: team for team in teams}
        self._members = members
        self._agents = agents
        self._tasks_by_id = {task.id: task for task in tasks}
        self._active_steps = active_steps
        self._active_runs = active_runs

    def build(self) -> list[dict[str, object]]:
        active_task_ids_by_agent: dict[UUID, set[UUID]] = defaultdict(set)
        active_step_count_by_agent: Counter[UUID] = Counter()
        active_run_count_by_agent: Counter[UUID] = Counter()
        active_team_ids_by_agent: dict[UUID, set[UUID]] = defaultdict(set)
        self._index_active_steps(
            active_task_ids_by_agent,
            active_step_count_by_agent,
            active_team_ids_by_agent,
        )
        self._index_active_runs(
            active_task_ids_by_agent,
            active_run_count_by_agent,
            active_team_ids_by_agent,
        )
        memberships_by_agent = self._memberships_by_agent()
        return [
            self._employee_payload(
                agent_id=agent_id,
                memberships=memberships_by_agent[agent_id],
                active_task_ids=active_task_ids_by_agent.get(agent_id, set()),
                active_team_ids=active_team_ids_by_agent.get(agent_id, set()),
                active_step_count=int(active_step_count_by_agent.get(agent_id, 0)),
                active_run_count=int(active_run_count_by_agent.get(agent_id, 0)),
            )
            for agent_id in sorted(memberships_by_agent, key=str)
        ]

    def _index_active_steps(
        self,
        active_task_ids_by_agent: dict[UUID, set[UUID]],
        active_step_count_by_agent: Counter[UUID],
        active_team_ids_by_agent: dict[UUID, set[UUID]],
    ) -> None:
        for step in self._active_steps:
            if step.assigned_agent_profile_id is None:
                continue
            agent_id = step.assigned_agent_profile_id
            active_task_ids_by_agent[agent_id].add(step.task_id)
            active_step_count_by_agent[agent_id] += 1
            task = self._tasks_by_id.get(step.task_id)
            if task is not None and task.agent_team_id is not None:
                active_team_ids_by_agent[agent_id].add(task.agent_team_id)

    def _index_active_runs(
        self,
        active_task_ids_by_agent: dict[UUID, set[UUID]],
        active_run_count_by_agent: Counter[UUID],
        active_team_ids_by_agent: dict[UUID, set[UUID]],
    ) -> None:
        for run in self._active_runs:
            if run.agent_profile_id is None or run.task_id is None:
                continue
            active_task_ids_by_agent[run.agent_profile_id].add(run.task_id)
            active_run_count_by_agent[run.agent_profile_id] += 1
            task = self._tasks_by_id.get(run.task_id)
            if task is not None and task.agent_team_id is not None:
                active_team_ids_by_agent[run.agent_profile_id].add(task.agent_team_id)

    def _memberships_by_agent(self) -> dict[UUID, list[AgentTeamMember]]:
        memberships_by_agent: dict[UUID, list[AgentTeamMember]] = defaultdict(list)
        for member in self._members:
            memberships_by_agent[member.agent_profile_id].append(member)
        return memberships_by_agent

    def _employee_payload(
        self,
        *,
        agent_id: UUID,
        memberships: list[AgentTeamMember],
        active_task_ids: set[UUID],
        active_team_ids: set[UUID],
        active_step_count: int,
        active_run_count: int,
    ) -> dict[str, object]:
        agent = self._agents.get(agent_id)
        capacity = sum(
            member.max_concurrent_tasks
            for member in memberships
            if member.status == "active" and member.accepts_tasks
        )
        active_task_count = len(active_task_ids)
        utilization = active_task_count / capacity if capacity > 0 else 0.0
        overloaded = capacity > 0 and active_task_count > capacity
        at_capacity = capacity > 0 and active_task_count >= capacity
        return {
            "agent_profile_id": agent_id,
            "agent_name": agent.name if agent is not None else None,
            "agent_role": agent.role if agent is not None else None,
            "agent_status": agent.status if agent is not None else None,
            "team_count": len({member.agent_team_id for member in memberships}),
            "active_team_count": len(active_team_ids),
            "team_ids": sorted({member.agent_team_id for member in memberships}, key=str),
            "active_team_ids": sorted(active_team_ids, key=str),
            "memberships": self._membership_items(memberships),
            "total_capacity": capacity,
            "active_task_count": active_task_count,
            "active_task_ids": sorted(active_task_ids, key=str),
            "active_step_count": active_step_count,
            "active_run_count": active_run_count,
            "utilization": round(utilization, 4),
            "at_capacity": at_capacity,
            "overloaded": overloaded,
            "blocked_reasons": employee_blocked_reasons(
                agent=agent,
                capacity=capacity,
                active_task_count=active_task_count,
                team_count=len({member.agent_team_id for member in memberships}),
                active_team_count=len(active_team_ids),
                overloaded=overloaded,
                at_capacity=at_capacity,
            ),
        }

    def _membership_items(self, memberships: list[AgentTeamMember]) -> list[dict[str, object]]:
        return [
            {
                "team_member_id": member.id,
                "team_id": member.agent_team_id,
                "team_name": self._teams_by_id[member.agent_team_id].name,
                "team_role": member.team_role,
                "department": member.department,
                "status": member.status,
                "accepts_tasks": member.accepts_tasks,
                "max_concurrent_tasks": member.max_concurrent_tasks,
            }
            for member in memberships
            if member.agent_team_id in self._teams_by_id
        ]


def employee_blocked_reasons(
    *,
    agent: AgentProfile | None,
    capacity: int,
    active_task_count: int,
    team_count: int,
    active_team_count: int,
    overloaded: bool,
    at_capacity: bool,
) -> list[str]:
    reasons: list[str] = []
    if agent is None:
        reasons.append("missing_agent_profile")
    elif agent.status != "active":
        reasons.append("agent_inactive")
    if capacity <= 0 and active_task_count > 0:
        reasons.append("agent_without_accepting_capacity")
    if overloaded:
        reasons.append("agent_over_capacity")
    elif at_capacity:
        reasons.append("agent_at_capacity")
    if team_count > 1 and active_team_count > 1 and (overloaded or at_capacity):
        reasons.append("agent_cross_team_capacity_pressure")
    return reasons
