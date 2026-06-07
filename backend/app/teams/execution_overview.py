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
REASSIGNABLE_SPECIALIST_STEP_STATUSES = {"blocked", "failed"}
RISK_LEVELS = ("critical", "high", "medium", "low")


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
        specialist_reassignments = _specialist_reassignments(members, agents, steps)
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
                specialist_reassignments=specialist_reassignments,
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
        pending_phase = _pending_phase(diagnostics)
        active_run_count = sum(1 for run in runs if run.status in ACTIVE_RUN_STATUSES)
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
    specialist_reassignments: list[dict[str, object]],
) -> dict[str, object]:
    task_status_counts = Counter(task.status for task in tasks)
    step_status_counts = Counter(step.status for step in steps)
    run_status_counts = Counter(run.status for run in runs)
    risk_counts = Counter(str(item["risk_level"]) for item in task_items)
    total_capacity = sum(
        int(item["max_concurrent_tasks"])
        for item in member_items
        if item["status"] == "active" and item["accepts_tasks"] is True
    )
    active_member_tasks = sum(int(item["active_task_count"]) for item in member_items)
    available_member_capacity = max(total_capacity - active_member_tasks, 0)
    bottlenecks = _summary_bottlenecks(
        task_items=task_items,
        member_items=member_items,
        staffing_gaps=staffing_gaps,
        specialist_reassignments=specialist_reassignments,
        steps=steps,
        runs=runs,
    )
    recommended_actions = _summary_recommended_actions(
        task_items=task_items,
        staffing_gaps=staffing_gaps,
        member_items=member_items,
        specialist_reassignments=specialist_reassignments,
    )
    delivery_health = _delivery_health(
        task_items=task_items,
        member_items=member_items,
        staffing_gaps=staffing_gaps,
        bottlenecks=bottlenecks,
        total_capacity=total_capacity,
        active_member_tasks=active_member_tasks,
        available_member_capacity=available_member_capacity,
    )
    return {
        "task_counts": dict(sorted(task_status_counts.items())),
        "step_counts": dict(sorted(step_status_counts.items())),
        "run_counts": dict(sorted(run_status_counts.items())),
        "total_tasks": len(tasks),
        "needs_attention_tasks": sum(1 for item in task_items if item["needs_attention"]),
        "blocked_tasks": sum(1 for item in task_items if item["blocked_reasons"]),
        "risk_counts": {level: risk_counts.get(level, 0) for level in RISK_LEVELS},
        "high_risk_task_count": sum(
            risk_counts.get(level, 0) for level in ("critical", "high")
        ),
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
        "specialist_reassignment_count": len(specialist_reassignments),
        "specialist_reassignments": specialist_reassignments,
        "total_member_capacity": total_capacity,
        "active_member_task_count": active_member_tasks,
        "available_member_capacity": available_member_capacity,
        "delivery_health": delivery_health,
        "bottlenecks": bottlenecks,
        "recommended_actions": recommended_actions,
        "intervention_plan": _operator_intervention_plan(
            recommended_actions=recommended_actions,
            bottlenecks=bottlenecks,
            staffing_gaps=staffing_gaps,
            specialist_reassignments=specialist_reassignments,
            task_items=task_items,
        ),
    }


def _specialist_reassignments(
    members: list[AgentTeamMember],
    agents: dict[UUID, AgentProfile],
    steps: list[TaskStep],
) -> list[dict[str, object]]:
    reassignments: list[dict[str, object]] = []
    for step in steps:
        if not _is_reassignable_specialist_step(step):
            continue
        replacement = _replacement_member_for_step(
            step=step,
            members=members,
            agents=agents,
        )
        if replacement is None:
            continue
        member, agent = replacement
        reassignments.append(
            {
                "task_id": step.task_id,
                "task_step_id": step.id,
                "step_status": step.status,
                "required_role": step.required_role,
                "required_skills": _string_list(step.required_skills),
                "current_agent_profile_id": step.assigned_agent_profile_id,
                "replacement_agent_profile_id": member.agent_profile_id,
                "replacement_agent_name": agent.name,
                "replacement_agent_role": agent.role,
                "replacement_team_role": member.team_role,
                "reason": "blocked_or_failed_specialist_step",
            }
        )
    return reassignments


def _is_reassignable_specialist_step(step: TaskStep) -> bool:
    if step.status not in REASSIGNABLE_SPECIALIST_STEP_STATUSES:
        return False
    if step.assigned_agent_profile_id is None:
        return False
    if _is_manager_role(step.required_role):
        return False
    work_package_id = step.work_package_id or ""
    return not work_package_id.startswith("manager-")


def _replacement_member_for_step(
    *,
    step: TaskStep,
    members: list[AgentTeamMember],
    agents: dict[UUID, AgentProfile],
) -> tuple[AgentTeamMember, AgentProfile] | None:
    candidates: list[tuple[int, int, str, AgentTeamMember, AgentProfile]] = []
    for member in members:
        if member.agent_profile_id == step.assigned_agent_profile_id:
            continue
        agent = agents.get(member.agent_profile_id)
        if agent is None or agent.workspace_id != step.workspace_id or agent.status != "active":
            continue
        if member.status != "active" or not member.accepts_tasks:
            continue
        if _is_manager_role(member.team_role) or _is_manager_role(agent.role):
            continue
        if not _member_matches_step(member=member, agent=agent, step=step):
            continue
        candidates.append(
            (
                _replacement_role_rank(member=member, agent=agent, step=step),
                member.order_index,
                str(member.id),
                member,
                agent,
            )
        )
    if not candidates:
        return None
    _, _, _, member, agent = sorted(candidates, key=lambda item: item[:3])[0]
    return member, agent


def _member_matches_step(
    *,
    member: AgentTeamMember,
    agent: AgentProfile,
    step: TaskStep,
) -> bool:
    required_role = step.required_role
    if required_role is not None and required_role not in {member.team_role, agent.role}:
        return False
    required_skills = _string_list(step.required_skills)
    if not required_skills:
        return True
    member_skills = {str(skill) for skill in member.skill_weights}
    agent_skills = {str(skill) for skill in agent.skills}
    return all(skill in member_skills or skill in agent_skills for skill in required_skills)


def _replacement_role_rank(
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
    return _dedupe_strings(actions)


def _summary_recommended_actions(
    *,
    task_items: list[dict[str, object]],
    staffing_gaps: list[dict[str, object]],
    member_items: list[dict[str, object]],
    specialist_reassignments: list[dict[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for gap in staffing_gaps:
        _add_summary_action(
            grouped,
            action=str(gap.get("recommended_action") or "add_or_hire_team_member"),
            task_ids=_uuid_list(gap.get("task_ids")),
        )
    for member in member_items:
        if member.get("overloaded") is True:
            _add_summary_action(grouped, action="rebalance_member_load", task_ids=[])
        if "member_not_accepting_tasks" in _string_list(member.get("blocked_reasons")):
            _add_summary_action(grouped, action="review_member_availability", task_ids=[])
    for reassignment in specialist_reassignments:
        task_id = reassignment.get("task_id")
        task_ids = [task_id] if isinstance(task_id, UUID) else []
        _add_summary_action(grouped, action="reassign_step", task_ids=task_ids)
    for task in task_items:
        task_id = task.get("task_id")
        task_ids = [task_id] if isinstance(task_id, UUID) else []
        for action in _string_list(task.get("recommended_actions")):
            _add_summary_action(grouped, action=action, task_ids=task_ids)

    return sorted(
        grouped.values(),
        key=lambda item: (-int(item["count"]), str(item["action"])),
    )


def _operator_intervention_plan(
    *,
    recommended_actions: list[dict[str, object]],
    bottlenecks: list[dict[str, object]],
    staffing_gaps: list[dict[str, object]],
    specialist_reassignments: list[dict[str, object]],
    task_items: list[dict[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for item in recommended_actions:
        action = item.get("action")
        if not isinstance(action, str) or not action:
            continue
        if action == "reassign_step":
            continue
        grouped[action] = {
            "action": action,
            "count": int(item.get("count") or 0),
            "task_ids": _uuid_list(item.get("task_ids")),
        }
    for bottleneck in bottlenecks:
        action = bottleneck.get("recommended_action")
        if not isinstance(action, str) or not action:
            continue
        if action == "reassign_step":
            continue
        item = grouped.setdefault(action, {"action": action, "count": 0, "task_ids": []})
        item["count"] = max(int(item["count"]), int(bottleneck.get("count") or 0))
        task_ids = set(_uuid_list(item.get("task_ids")))
        task_ids.update(_uuid_list(bottleneck.get("task_ids")))
        item["task_ids"] = sorted(task_ids, key=str)

    plan: list[dict[str, object]] = []
    for item in grouped.values():
        action = str(item["action"])
        task_ids = _uuid_list(item.get("task_ids"))
        task_step_ids = _intervention_step_ids(action, staffing_gaps)
        related_bottlenecks = [
            bottleneck
            for bottleneck in bottlenecks
            if bottleneck.get("recommended_action") == action
        ]
        severity = _highest_severity(
            [str(bottleneck.get("severity")) for bottleneck in related_bottlenecks]
        )
        reason_codes = _intervention_reason_codes(
            action=action,
            task_ids=task_ids,
            task_items=task_items,
            bottlenecks=related_bottlenecks,
        )
        plan.append(
            {
                "action": action,
                "category": _intervention_category(action),
                "severity": severity,
                "priority": _intervention_priority(severity, int(item["count"])),
                "count": int(item["count"]),
                "task_ids": task_ids,
                "task_step_ids": task_step_ids,
                "automation": _intervention_automation(action),
                "operator_action": _operator_action_name(action),
                "api_route": _intervention_api_route(action),
                "payload_template": _intervention_payload_template(
                    action,
                    task_step_ids=task_step_ids,
                ),
                "reason_codes": reason_codes,
            }
        )
    return sorted(
        [*plan, *_reassign_step_interventions(specialist_reassignments)],
        key=lambda item: (-int(item["priority"]), str(item["action"])),
    )


def _reassign_step_interventions(
    specialist_reassignments: list[dict[str, object]],
) -> list[dict[str, object]]:
    interventions: list[dict[str, object]] = []
    for reassignment in specialist_reassignments:
        task_id = reassignment.get("task_id")
        task_step_id = reassignment.get("task_step_id")
        agent_profile_id = reassignment.get("replacement_agent_profile_id")
        if not (
            isinstance(task_id, UUID)
            and isinstance(task_step_id, UUID)
            and isinstance(agent_profile_id, UUID)
        ):
            continue
        step_status = str(reassignment.get("step_status") or "unknown")
        severity = "high" if step_status == "failed" else "medium"
        reason = str(reassignment.get("reason") or "blocked_or_failed_specialist_step")
        interventions.append(
            {
                "action": "reassign_step",
                "category": "execution_flow",
                "severity": severity,
                "priority": _intervention_priority(severity, 1),
                "count": 1,
                "task_ids": [task_id],
                "task_step_ids": [task_step_id],
                "agent_profile_id": agent_profile_id,
                "current_agent_profile_id": reassignment.get("current_agent_profile_id"),
                "replacement_agent": {
                    "id": agent_profile_id,
                    "name": reassignment.get("replacement_agent_name"),
                    "role": reassignment.get("replacement_agent_role"),
                    "team_role": reassignment.get("replacement_team_role"),
                },
                "automation": "team_operator_action",
                "operator_action": "reassign_step",
                "api_route": _intervention_api_route("reassign_step"),
                "payload_template": {
                    "action": "reassign_step",
                    "task_step_ids": [task_step_id],
                    "agent_profile_id": agent_profile_id,
                    "reason": "team_execution_overview",
                    "metadata": {
                        "source": "team_execution_overview",
                        "reassignment_reason": reason,
                    },
                },
                "reason_codes": _dedupe_strings(
                    [
                        reason,
                        f"step_status:{step_status}",
                        f"required_role:{reassignment['required_role']}"
                        if isinstance(reassignment.get("required_role"), str)
                        else "required_role:unknown",
                    ]
                ),
            }
        )
    return interventions


def _intervention_step_ids(
    action: str,
    staffing_gaps: list[dict[str, object]],
) -> list[UUID]:
    if action != "add_or_hire_team_member":
        return []
    return sorted(
        {
            step_id
            for gap in staffing_gaps
            for step_id in _uuid_list(gap.get("task_step_ids"))
        },
        key=str,
    )


def _intervention_reason_codes(
    *,
    action: str,
    task_ids: list[UUID],
    task_items: list[dict[str, object]],
    bottlenecks: list[dict[str, object]],
) -> list[str]:
    task_id_set = set(task_ids)
    reasons = [
        str(bottleneck["code"])
        for bottleneck in bottlenecks
        if isinstance(bottleneck.get("code"), str)
    ]
    if action == "add_or_hire_team_member":
        reasons.append("staffing_gap")
    for task in task_items:
        task_id = task.get("task_id")
        if not isinstance(task_id, UUID) or task_id not in task_id_set:
            continue
        reasons.extend(_string_list(task.get("blocked_reasons")))
        if isinstance(task.get("risk_level"), str):
            reasons.append(f"risk:{task['risk_level']}")
    return _dedupe_strings(reasons)


def _highest_severity(severities: list[str]) -> str:
    valid = [
        severity
        for severity in severities
        if severity in {"critical", "high", "medium", "low"}
    ]
    if not valid:
        return "medium"
    return sorted(valid, key=_severity_rank)[0]


def _intervention_priority(severity: str, count: int) -> int:
    base = {
        "critical": 100,
        "high": 80,
        "medium": 50,
        "low": 25,
    }.get(severity, 40)
    return base + min(max(count, 0), 10)


def _intervention_category(action: str) -> str:
    if action == "add_or_hire_team_member":
        return "staffing"
    if action in {"request_manager_review", "track_revision_follow_up"}:
        return "manager_review"
    if action in {"rebalance_member_load", "review_member_availability"}:
        return "team_capacity"
    if action in {
        "reassign_step",
        "schedule_downstream_steps",
        "unblock_or_reassign_specialist_work",
    }:
        return "execution_flow"
    if action in {"inspect_runtime_capacity", "review_pending_approvals"}:
        return "runtime_operations"
    return "diagnostics"


def _intervention_automation(action: str) -> str:
    if action in {
        "request_manager_review",
        "reassign_step",
        "schedule_downstream_steps",
        "requeue_blocked_steps",
    }:
        return "team_operator_action"
    if action == "add_or_hire_team_member":
        return "talent_market"
    if action in {
        "monitor_specialist_execution",
        "inspect_task_diagnostics",
        "review_high_risk_tasks",
    }:
        return "diagnostic"
    return "manual"


def _operator_action_name(action: str) -> str | None:
    if action in {
        "request_manager_review",
        "reassign_step",
        "schedule_downstream_steps",
        "requeue_blocked_steps",
    }:
        return action
    return None


def _intervention_api_route(action: str) -> str:
    if action == "add_or_hire_team_member":
        return (
            "POST /api/v1/workspaces/{workspace_id}/tasks/{task_id}/"
            "talent-market/recommendations"
        )
    if _operator_action_name(action) is not None:
        return "POST /api/v1/workspaces/{workspace_id}/teams/{team_id}/operator-actions"
    if action in {
        "monitor_specialist_execution",
        "inspect_task_diagnostics",
        "review_high_risk_tasks",
    }:
        return "GET /api/v1/workspaces/{workspace_id}/tasks/{task_id}/execution-diagnostics"
    return "GET /api/v1/workspaces/{workspace_id}/teams/{team_id}/execution-overview"


def _intervention_payload_template(
    action: str,
    *,
    task_step_ids: list[UUID],
) -> dict[str, object]:
    operator_action = _operator_action_name(action)
    if operator_action is not None:
        return {
            "action": operator_action,
            "task_step_ids": task_step_ids,
            "reason": "team_execution_overview",
            "metadata": {"source": "team_execution_overview"},
        }
    if action == "add_or_hire_team_member":
        return {"max_candidates_per_role": 3}
    return {}


def _summary_bottlenecks(
    *,
    task_items: list[dict[str, object]],
    member_items: list[dict[str, object]],
    staffing_gaps: list[dict[str, object]],
    specialist_reassignments: list[dict[str, object]],
    steps: list[TaskStep],
    runs: list[AgentRun],
) -> list[dict[str, object]]:
    bottlenecks: list[dict[str, object]] = []
    if staffing_gaps:
        task_ids = sorted(
            {
                task_id
                for gap in staffing_gaps
                for task_id in _uuid_list(gap.get("task_ids"))
            },
            key=str,
        )
        bottlenecks.append(
            _bottleneck(
                code="staffing_gap",
                severity="high",
                count=len(staffing_gaps),
                task_ids=task_ids,
                recommended_action="add_or_hire_team_member",
            )
        )

    if specialist_reassignments:
        task_ids = sorted(
            {
                task_id
                for reassignment in specialist_reassignments
                if isinstance((task_id := reassignment.get("task_id")), UUID)
            },
            key=str,
        )
        bottlenecks.append(
            _bottleneck(
                code="specialist_step_reassignment",
                severity="high"
                if any(
                    reassignment.get("step_status") == "failed"
                    for reassignment in specialist_reassignments
                )
                else "medium",
                count=len(specialist_reassignments),
                task_ids=task_ids,
                recommended_action="reassign_step",
            )
        )

    overloaded_members = [
        member for member in member_items if member.get("overloaded") is True
    ]
    if overloaded_members:
        bottlenecks.append(
            _bottleneck(
                code="member_over_capacity",
                severity="high",
                count=len(overloaded_members),
                task_ids=[],
                recommended_action="rebalance_member_load",
            )
        )

    blocked_tasks = [task for task in task_items if task.get("blocked_reasons")]
    if blocked_tasks:
        bottlenecks.append(
            _bottleneck(
                code="blocked_tasks",
                severity="high"
                if any(task.get("risk_level") in {"critical", "high"} for task in blocked_tasks)
                else "medium",
                count=len(blocked_tasks),
                task_ids=_task_item_ids(blocked_tasks),
                recommended_action="inspect_task_diagnostics",
            )
        )

    high_risk_tasks = [
        task for task in task_items if task.get("risk_level") in {"critical", "high"}
    ]
    if high_risk_tasks:
        bottlenecks.append(
            _bottleneck(
                code="high_risk_tasks",
                severity="critical"
                if any(task.get("risk_level") == "critical" for task in high_risk_tasks)
                else "high",
                count=len(high_risk_tasks),
                task_ids=_task_item_ids(high_risk_tasks),
                recommended_action="review_high_risk_tasks",
            )
        )

    waiting_runtime_count = sum(1 for run in runs if run.status == "waiting_runtime")
    if waiting_runtime_count:
        bottlenecks.append(
            _bottleneck(
                code="runtime_wait",
                severity="medium",
                count=waiting_runtime_count,
                task_ids=sorted(
                    {
                        run.task_id
                        for run in runs
                        if run.status == "waiting_runtime" and run.task_id
                    },
                    key=str,
                ),
                recommended_action="inspect_runtime_capacity",
            )
        )

    waiting_approval_steps = [step for step in steps if step.status == "waiting_approval"]
    if waiting_approval_steps:
        bottlenecks.append(
            _bottleneck(
                code="approval_wait",
                severity="medium",
                count=len(waiting_approval_steps),
                task_ids=sorted({step.task_id for step in waiting_approval_steps}, key=str),
                recommended_action="review_pending_approvals",
            )
        )

    return sorted(
        bottlenecks,
        key=lambda item: (_severity_rank(str(item["severity"])), str(item["code"])),
    )


def _delivery_health(
    *,
    task_items: list[dict[str, object]],
    member_items: list[dict[str, object]],
    staffing_gaps: list[dict[str, object]],
    bottlenecks: list[dict[str, object]],
    total_capacity: int,
    active_member_tasks: int,
    available_member_capacity: int,
) -> dict[str, object]:
    reasons: list[str] = []
    score = 100
    high_risk_count = sum(
        1 for task in task_items if task.get("risk_level") in {"critical", "high"}
    )
    blocked_task_count = sum(1 for task in task_items if task.get("blocked_reasons"))
    overloaded_count = sum(1 for member in member_items if member.get("overloaded") is True)
    critical_bottleneck = any(item.get("severity") == "critical" for item in bottlenecks)

    if staffing_gaps:
        score -= 20
        reasons.append("staffing_gap")
    if high_risk_count:
        score -= min(35, high_risk_count * 25)
        reasons.append("high_risk_tasks")
    if blocked_task_count:
        score -= min(25, blocked_task_count * 15)
        reasons.append("blocked_tasks")
    if overloaded_count:
        score -= min(20, overloaded_count * 15)
        reasons.append("member_over_capacity")
    if total_capacity > 0 and active_member_tasks >= total_capacity:
        score -= 10
        reasons.append("team_capacity_full")

    score = max(score, 0)
    if critical_bottleneck or score <= 40:
        status = "critical"
    elif score <= 70:
        status = "degraded"
    elif score < 95:
        status = "attention"
    else:
        status = "healthy"

    return {
        "status": status,
        "score": score,
        "reasons": _dedupe_strings(reasons),
        "bottleneck_count": len(bottlenecks),
        "high_risk_task_count": high_risk_count,
        "blocked_task_count": blocked_task_count,
        "overloaded_member_count": overloaded_count,
        "capacity_utilization": round(
            active_member_tasks / total_capacity,
            4,
        )
        if total_capacity > 0
        else 0.0,
        "available_member_capacity": available_member_capacity,
    }


def _bottleneck(
    *,
    code: str,
    severity: str,
    count: int,
    task_ids: list[UUID],
    recommended_action: str,
) -> dict[str, object]:
    return {
        "code": code,
        "severity": severity,
        "count": count,
        "task_ids": task_ids,
        "recommended_action": recommended_action,
    }


def _task_item_ids(task_items: list[dict[str, object]]) -> list[UUID]:
    task_ids = [task.get("task_id") for task in task_items]
    return sorted([task_id for task_id in task_ids if isinstance(task_id, UUID)], key=str)


def _severity_rank(severity: str) -> int:
    return {
        "critical": 0,
        "high": 1,
        "medium": 2,
        "low": 3,
    }.get(severity, 4)


def _add_summary_action(
    grouped: dict[str, dict[str, object]],
    *,
    action: str,
    task_ids: list[UUID],
) -> None:
    item = grouped.setdefault(action, {"action": action, "count": 0, "task_ids": []})
    item["count"] = int(item["count"]) + 1
    existing = item["task_ids"] if isinstance(item["task_ids"], list) else []
    merged = {task_id for task_id in existing if isinstance(task_id, UUID)}
    merged.update(task_ids)
    item["task_ids"] = sorted(merged, key=str)


def _uuid_list(value: object) -> list[UUID]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, UUID)]


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


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


def _is_manager_role(value: str | None) -> bool:
    return value in {"project_manager", "manager", "team_manager"}
