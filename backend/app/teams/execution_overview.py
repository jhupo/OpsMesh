from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.tasks.manager_diagnostics import TaskManagerDiagnosticsService
from backend.app.tasks.models import Task, TaskStep
from backend.app.teams.execution_overview_constants import ACTIVE_RUN_STATUSES
from backend.app.teams.execution_overview_members import (
    active_run_phase_counts,
    member_items,
    specialist_reassignments,
    staffing_gaps,
)
from backend.app.teams.execution_overview_repository import TeamExecutionOverviewRepository
from backend.app.teams.execution_overview_summary import overview_summary
from backend.app.teams.execution_overview_utils import dedupe_strings, string_list


class TeamExecutionOverviewService:
    """Build a team-level execution control-plane view for reusable agent teams."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = TeamExecutionOverviewRepository(session)

    def get_overview(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        include_completed: bool = False,
    ) -> dict[str, object] | None:
        team = self._repo.team(workspace_id, team_id)
        if team is None:
            return None

        members = self._repo.members(workspace_id, team_id)
        agent_ids = {member.agent_profile_id for member in members}
        if team.manager_agent_profile_id is not None:
            agent_ids.add(team.manager_agent_profile_id)
        agents = self._repo.agents(workspace_id, agent_ids)
        tasks = self._repo.tasks(
            workspace_id=workspace_id,
            team_id=team_id,
            include_completed=include_completed,
        )
        task_ids = [task.id for task in tasks]
        steps = self._repo.steps(workspace_id, task_ids)
        runs = self._repo.runs(workspace_id, task_ids)
        latest_events = self._repo.latest_events(workspace_id, runs)
        steps_by_task = _group_steps_by_task(steps)
        runs_by_task = _group_runs_by_task(runs)
        member_items_result = member_items(
            members,
            agents,
            steps,
            runs,
            latest_events,
            workspace_active_task_ids_by_agent=self._repo.workspace_active_task_ids_by_agent(
                workspace_id,
                {member.agent_profile_id for member in members},
            ),
        )
        staffing_gaps_result = staffing_gaps(members, agents, steps)
        specialist_reassignments_result = specialist_reassignments(members, agents, steps)
        task_items = [
            self._task_item(
                workspace_id=workspace_id,
                task=task,
                steps=steps_by_task.get(task.id, []),
                runs=runs_by_task.get(task.id, []),
                latest_events=latest_events,
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
            "summary": overview_summary(
                tasks=tasks,
                steps=steps,
                runs=runs,
                member_items=member_items_result,
                task_items=task_items,
                staffing_gaps=staffing_gaps_result,
                specialist_reassignments=specialist_reassignments_result,
            ),
            "members": member_items_result,
            "tasks": task_items,
            "staffing_gaps": staffing_gaps_result,
        }

    def _task_item(
        self,
        *,
        workspace_id: UUID,
        task: Task,
        steps: list[TaskStep],
        runs: list[AgentRun],
        latest_events: dict[UUID, RunEvent],
    ) -> dict[str, object]:
        diagnostics = TaskManagerDiagnosticsService(self._session).get_diagnostics(
            workspace_id=workspace_id,
            task_id=task.id,
        )
        blocked_reasons = (
            string_list(diagnostics.get("blocked_reasons"))
            if isinstance(diagnostics, dict)
            else []
        )
        summary = diagnostics.get("summary") if isinstance(diagnostics, dict) else {}
        summary_status = (
            str(summary.get("status"))
            if isinstance(summary, dict) and summary.get("status") is not None
            else "unknown"
        )
        pending_phase = _pending_phase(diagnostics)
        active_run_count = sum(1 for run in runs if run.status in ACTIVE_RUN_STATUSES)
        active_run_phase_counts_result = active_run_phase_counts(runs, latest_events)
        needs_attention = summary_status != "healthy" or bool(blocked_reasons)
        risk_level = _task_risk_level(
            task=task,
            blocked_reasons=blocked_reasons,
            summary_status=summary_status,
            needs_attention=needs_attention,
        )
        recommended_actions = _task_recommended_actions(
            blocked_reasons=blocked_reasons,
            pending_phase=pending_phase,
            active_run_count=active_run_count,
            needs_attention=needs_attention,
        )
        return {
            "task_id": task.id,
            "title": task.title,
            "status": task.status,
            "priority": task.priority,
            "domain_type": task.domain_type,
            "summary_status": summary_status,
            "pending_phase": pending_phase,
            "risk_level": risk_level,
            "attention_score": _attention_score(task.priority, risk_level, blocked_reasons),
            "needs_attention": needs_attention,
            "blocked_reasons": blocked_reasons,
            "recommended_actions": recommended_actions,
            "step_status_counts": dict(sorted(Counter(step.status for step in steps).items())),
            "active_run_count": active_run_count,
            "active_run_phase_counts": active_run_phase_counts_result,
            "last_activity_at": task.updated_at,
        }


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


def _task_risk_level(
    *,
    task: Task,
    blocked_reasons: list[str],
    summary_status: str,
    needs_attention: bool,
) -> str:
    reasons = set(blocked_reasons)
    if task.status == "failed" or "missing_manager" in reasons:
        return "critical"
    if reasons & {
        "missing_manager_planning_step",
        "missing_manager_summary_step",
        "acceptance_decision_missing",
        "follow_up_missing",
        "follow_up_incomplete",
    }:
        return "high"
    if task.priority >= 8 and needs_attention:
        return "high"
    if needs_attention or summary_status == "attention":
        return "medium"
    return "low"


def _attention_score(priority: int, risk_level: str, blocked_reasons: list[str]) -> int:
    risk_weight = {
        "critical": 100,
        "high": 70,
        "medium": 40,
        "low": 10,
    }.get(risk_level, 0)
    return risk_weight + max(priority, 0) * 10 + len(blocked_reasons) * 5


def _task_recommended_actions(
    *,
    blocked_reasons: list[str],
    pending_phase: str,
    active_run_count: int,
    needs_attention: bool,
) -> list[str]:
    reasons = set(blocked_reasons)
    actions: list[str] = []
    if "missing_manager" in reasons:
        actions.append("assign_manager")
    if reasons & {
        "missing_manager_planning_step",
        "missing_manager_summary_step",
        "acceptance_decision_missing",
        "follow_up_missing",
    }:
        actions.append("request_manager_review")
    if "follow_up_incomplete" in reasons:
        actions.append("track_revision_follow_up")
    if "specialist_steps_incomplete" in reasons and active_run_count == 0:
        actions.append("unblock_or_reassign_specialist_work")
    elif "specialist_steps_incomplete" in reasons:
        actions.append("monitor_specialist_execution")
    if needs_attention and pending_phase == "unknown":
        actions.append("inspect_task_diagnostics")
    return dedupe_strings(actions)


def _agent_summary(agent: AgentProfile | None) -> dict[str, object] | None:
    if agent is None:
        return None
    return {
        "id": agent.id,
        "name": agent.name,
        "role": agent.role,
        "status": agent.status,
    }


