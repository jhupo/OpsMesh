from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.runs.models import AgentRun
from backend.app.tasks.manager_diagnostics import TaskManagerDiagnosticsService
from backend.app.tasks.models import Task, TaskStep
from backend.app.teams.models import AgentTeam, AgentTeamMember

ACTIVE_STEP_STATUSES = {"queued", "running", "waiting_approval", "blocked"}
ACTIVE_RUN_STATUSES = {"queued", "running", "waiting_runtime"}
DONE_TASK_STATUSES = {"completed", "cancelled", "canceled"}


class TeamExecutionOverviewService:
    """Build a team-level execution control-plane view for reusable agent teams."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_overview(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        include_completed: bool = False,
    ) -> dict[str, object] | None:
        team = self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == team_id,
            )
        )
        if team is None:
            return None

        members = self._members(workspace_id, team_id)
        agent_ids = {member.agent_profile_id for member in members}
        if team.manager_agent_profile_id is not None:
            agent_ids.add(team.manager_agent_profile_id)
        agents = self._agents(workspace_id, agent_ids)
        tasks = self._tasks(
            workspace_id=workspace_id,
            team_id=team_id,
            include_completed=include_completed,
        )
        task_ids = [task.id for task in tasks]
        steps = self._steps(workspace_id, task_ids)
        runs = self._runs(workspace_id, task_ids)
        steps_by_task = _group_steps_by_task(steps)
        runs_by_task = _group_runs_by_task(runs)
        member_items = _member_items(members, agents, steps, runs)
        staffing_gaps = _staffing_gaps(members, agents, steps)
        task_items = [
            self._task_item(
                workspace_id=workspace_id,
                task=task,
                steps=steps_by_task.get(task.id, []),
                runs=runs_by_task.get(task.id, []),
            )
            for task in tasks
        ]
        return {
            "workspace_id": workspace_id,
            "team_id": team.id,
            "generated_at": datetime.now(UTC),
            "team": {
                "id": team.id,
                "name": team.name,
                "team_type": team.team_type,
                "status": team.status,
                "runtime_space_id": team.runtime_space_id,
            },
            "manager_agent": _agent_summary(agents.get(team.manager_agent_profile_id))
            if team.manager_agent_profile_id is not None
            else None,
            "summary": _overview_summary(
                tasks=tasks,
                steps=steps,
                runs=runs,
                member_items=member_items,
                task_items=task_items,
                staffing_gaps=staffing_gaps,
            ),
            "members": member_items,
            "tasks": task_items,
            "staffing_gaps": staffing_gaps,
        }

    def _members(self, workspace_id: UUID, team_id: UUID) -> list[AgentTeamMember]:
        return list(
            self._session.scalars(
                select(AgentTeamMember)
                .where(
                    AgentTeamMember.workspace_id == workspace_id,
                    AgentTeamMember.agent_team_id == team_id,
                )
                .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.id.asc())
            )
        )

    def _agents(
        self,
        workspace_id: UUID,
        agent_ids: set[UUID],
    ) -> dict[UUID, AgentProfile]:
        if not agent_ids:
            return {}
        agents = self._session.scalars(
            select(AgentProfile).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.id.in_(agent_ids),
            )
        ).all()
        return {agent.id: agent for agent in agents}

    def _tasks(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        include_completed: bool,
    ) -> list[Task]:
        statement = select(Task).where(
            Task.workspace_id == workspace_id,
            Task.agent_team_id == team_id,
        )
        if not include_completed:
            statement = statement.where(~Task.status.in_(DONE_TASK_STATUSES))
        return list(
            self._session.scalars(
                statement.order_by(Task.priority.desc(), Task.updated_at.desc(), Task.id.asc())
            )
        )

    def _steps(self, workspace_id: UUID, task_ids: list[UUID]) -> list[TaskStep]:
        if not task_ids:
            return []
        return list(
            self._session.scalars(
                select(TaskStep)
                .where(
                    TaskStep.workspace_id == workspace_id,
                    TaskStep.task_id.in_(task_ids),
                )
                .order_by(TaskStep.order_index.asc(), TaskStep.id.asc())
            )
        )

    def _runs(self, workspace_id: UUID, task_ids: list[UUID]) -> list[AgentRun]:
        if not task_ids:
            return []
        return list(
            self._session.scalars(
                select(AgentRun)
                .where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.task_id.in_(task_ids),
                )
                .order_by(AgentRun.created_at.desc(), AgentRun.id.asc())
            )
        )

    def _task_item(
        self,
        *,
        workspace_id: UUID,
        task: Task,
        steps: list[TaskStep],
        runs: list[AgentRun],
    ) -> dict[str, object]:
        diagnostics = TaskManagerDiagnosticsService(self._session).get_diagnostics(
            workspace_id=workspace_id,
            task_id=task.id,
        )
        blocked_reasons = (
            _string_list(diagnostics.get("blocked_reasons"))
            if isinstance(diagnostics, dict)
            else []
        )
        summary = diagnostics.get("summary") if isinstance(diagnostics, dict) else {}
        summary_status = (
            str(summary.get("status"))
            if isinstance(summary, dict) and summary.get("status") is not None
            else "unknown"
        )
        return {
            "task_id": task.id,
            "title": task.title,
            "status": task.status,
            "priority": task.priority,
            "domain_type": task.domain_type,
            "summary_status": summary_status,
            "pending_phase": _pending_phase(diagnostics),
            "needs_attention": summary_status != "healthy" or bool(blocked_reasons),
            "blocked_reasons": blocked_reasons,
            "step_status_counts": dict(sorted(Counter(step.status for step in steps).items())),
            "active_run_count": sum(1 for run in runs if run.status in ACTIVE_RUN_STATUSES),
            "last_activity_at": task.updated_at,
        }


def _member_items(
    members: list[AgentTeamMember],
    agents: dict[UUID, AgentProfile],
    steps: list[TaskStep],
    runs: list[AgentRun],
) -> list[dict[str, object]]:
    active_steps_by_agent: dict[UUID, list[TaskStep]] = defaultdict(list)
    active_runs_by_agent: dict[UUID, list[AgentRun]] = defaultdict(list)
    for step in steps:
        if step.assigned_agent_profile_id is None or step.status not in ACTIVE_STEP_STATUSES:
            continue
        active_steps_by_agent[step.assigned_agent_profile_id].append(step)
    for run in runs:
        if run.agent_profile_id is None or run.status not in ACTIVE_RUN_STATUSES:
            continue
        active_runs_by_agent[run.agent_profile_id].append(run)

    items: list[dict[str, object]] = []
    for member in members:
        agent = agents.get(member.agent_profile_id)
        active_steps = active_steps_by_agent.get(member.agent_profile_id, [])
        active_runs = active_runs_by_agent.get(member.agent_profile_id, [])
        active_task_ids = {step.task_id for step in active_steps}
        utilization = (
            len(active_task_ids) / member.max_concurrent_tasks
            if member.max_concurrent_tasks > 0
            else 0.0
        )
        blocked_reasons = _member_blocked_reasons(
            member=member,
            agent=agent,
            active_task_count=len(active_task_ids),
        )
        items.append(
            {
                "team_member_id": member.id,
                "agent_profile_id": member.agent_profile_id,
                "agent_name": agent.name if agent is not None else None,
                "agent_role": agent.role if agent is not None else None,
                "team_role": member.team_role,
                "department": member.department,
                "status": member.status,
                "accepts_tasks": member.accepts_tasks,
                "max_concurrent_tasks": member.max_concurrent_tasks,
                "active_task_count": len(active_task_ids),
                "active_step_count": len(active_steps),
                "active_run_count": len(active_runs),
                "utilization": round(utilization, 4),
                "overloaded": len(active_task_ids) > member.max_concurrent_tasks,
                "blocked_reasons": blocked_reasons,
            }
        )
    return items


def _member_blocked_reasons(
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
    return reasons


def _overview_summary(
    *,
    tasks: list[Task],
    steps: list[TaskStep],
    runs: list[AgentRun],
    member_items: list[dict[str, object]],
    task_items: list[dict[str, object]],
    staffing_gaps: list[dict[str, object]],
) -> dict[str, object]:
    task_status_counts = Counter(task.status for task in tasks)
    step_status_counts = Counter(step.status for step in steps)
    run_status_counts = Counter(run.status for run in runs)
    total_capacity = sum(
        int(item["max_concurrent_tasks"])
        for item in member_items
        if item["status"] == "active" and item["accepts_tasks"] is True
    )
    active_member_tasks = sum(int(item["active_task_count"]) for item in member_items)
    return {
        "task_counts": dict(sorted(task_status_counts.items())),
        "step_counts": dict(sorted(step_status_counts.items())),
        "run_counts": dict(sorted(run_status_counts.items())),
        "total_tasks": len(tasks),
        "needs_attention_tasks": sum(1 for item in task_items if item["needs_attention"]),
        "blocked_tasks": sum(1 for item in task_items if item["blocked_reasons"]),
        "member_count": len(member_items),
        "active_member_count": sum(1 for item in member_items if item["status"] == "active"),
        "accepting_member_count": sum(
            1
            for item in member_items
            if item["status"] == "active" and item["accepts_tasks"] is True
        ),
        "overloaded_member_count": sum(1 for item in member_items if item["overloaded"]),
        "staffing_gap_count": len(staffing_gaps),
        "staffing_gap_step_count": sum(int(item["step_count"]) for item in staffing_gaps),
        "total_member_capacity": total_capacity,
        "active_member_task_count": active_member_tasks,
        "available_member_capacity": max(total_capacity - active_member_tasks, 0),
    }


def _staffing_gaps(
    members: list[AgentTeamMember],
    agents: dict[UUID, AgentProfile],
    steps: list[TaskStep],
) -> list[dict[str, object]]:
    grouped_steps: dict[tuple[str | None, tuple[str, ...]], list[TaskStep]] = defaultdict(list)
    for step in steps:
        if step.status not in ACTIVE_STEP_STATUSES or step.assigned_agent_profile_id is not None:
            continue
        required_role = step.required_role or None
        required_skills = tuple(sorted(_string_list(step.required_skills)))
        if required_role is None and not required_skills:
            continue
        matching_member_count = _matching_member_count(
            members=members,
            agents=agents,
            required_role=required_role,
            required_skills=required_skills,
        )
        if matching_member_count > 0:
            continue
        grouped_steps[(required_role, required_skills)].append(step)

    gaps: list[dict[str, object]] = []
    for (required_role, required_skills), gap_steps in sorted(
        grouped_steps.items(),
        key=lambda item: (
            item[0][0] or "",
            ",".join(item[0][1]),
            min(step.order_index for step in item[1]),
        ),
    ):
        task_ids = sorted({step.task_id for step in gap_steps}, key=str)
        gaps.append(
            {
                "required_role": required_role,
                "required_skills": list(required_skills),
                "step_count": len(gap_steps),
                "task_count": len(task_ids),
                "task_ids": task_ids,
                "task_step_ids": [step.id for step in gap_steps],
                "matching_member_count": 0,
                "recommended_action": "add_or_hire_team_member",
            }
        )
    return gaps


def _matching_member_count(
    *,
    members: list[AgentTeamMember],
    agents: dict[UUID, AgentProfile],
    required_role: str | None,
    required_skills: tuple[str, ...],
) -> int:
    count = 0
    for member in members:
        agent = agents.get(member.agent_profile_id)
        if agent is None or agent.status != "active":
            continue
        if member.status != "active" or not member.accepts_tasks:
            continue
        if required_role is not None and required_role not in {member.team_role, agent.role}:
            continue
        member_skills = {str(skill) for skill in member.skill_weights}
        if any(skill not in member_skills for skill in required_skills):
            continue
        count += 1
    return count


def _group_steps_by_task(steps: list[TaskStep]) -> dict[UUID, list[TaskStep]]:
    grouped: dict[UUID, list[TaskStep]] = defaultdict(list)
    for step in steps:
        grouped[step.task_id].append(step)
    return grouped


def _group_runs_by_task(runs: list[AgentRun]) -> dict[UUID, list[AgentRun]]:
    grouped: dict[UUID, list[AgentRun]] = defaultdict(list)
    for run in runs:
        if run.task_id is not None:
            grouped[run.task_id].append(run)
    return grouped


def _pending_phase(diagnostics: object) -> str:
    if not isinstance(diagnostics, dict):
        return "unknown"
    handoff_chain = diagnostics.get("handoff_chain")
    if isinstance(handoff_chain, list):
        for phase in handoff_chain:
            if not isinstance(phase, dict):
                continue
            if phase.get("status") not in {"completed", "not_required"}:
                value = phase.get("phase")
                return value if isinstance(value, str) else "unknown"
    return "none"


def _agent_summary(agent: AgentProfile | None) -> dict[str, object] | None:
    if agent is None:
        return None
    return {
        "id": agent.id,
        "name": agent.name,
        "role": agent.role,
        "status": agent.status,
    }


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
