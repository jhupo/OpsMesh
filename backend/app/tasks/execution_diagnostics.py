from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.tasks.models import Task, TaskStep

ACTIVE_RUN_STATUSES = {
    RunStatus.QUEUED.value,
    RunStatus.RUNNING.value,
    RunStatus.WAITING_RUNTIME.value,
    RunStatus.WAITING_APPROVAL.value,
}


class TaskExecutionDiagnosticsService:
    """Explain the durable execution state for a team-backed task."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_diagnostics(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
    ) -> dict[str, object] | None:
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            return None

        steps = self._steps(workspace_id, task.id)
        runs_by_step_id = self._runs_by_step_id(workspace_id, task.id)
        agents = self._agent_map(workspace_id, steps)
        step_by_id = {step.id: step for step in steps}
        downstream_by_step_id = _downstream_map(steps)
        step_payloads = [
            self._step_payload(
                step,
                agents=agents,
                step_by_id=step_by_id,
                runs=runs_by_step_id.get(step.id, []),
            )
            for step in steps
        ]
        step_payload_by_id = {
            step_payload["task_step_id"]: step_payload for step_payload in step_payloads
        }
        for step_payload in step_payloads:
            step_payload["handoff"] = _handoff_state(
                step_payload,
                step_payload_by_id=step_payload_by_id,
                downstream_by_step_id=downstream_by_step_id,
            )
        return {
            "workspace_id": workspace_id,
            "task_id": task.id,
            "generated_at": datetime.now(UTC),
            "task": {
                "title": task.title,
                "status": task.status,
                "priority": task.priority,
                "domain_type": task.domain_type,
                "agent_team_id": task.agent_team_id,
                "runtime_space_id": task.runtime_space_id,
                "has_project_plan": task.project_plan is not None,
                "has_team_snapshot": task.team_snapshot is not None,
            },
            "summary": self._summary(task, step_payloads, runs_by_step_id),
            "steps": step_payloads,
        }

    def _step_payload(
        self,
        step: TaskStep,
        *,
        agents: dict[UUID, AgentProfile],
        step_by_id: dict[UUID, TaskStep],
        runs: list[AgentRun],
    ) -> dict[str, object]:
        dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
        dependency_state = _dependency_state(step, step_by_id)
        assigned_agent = (
            agents.get(step.assigned_agent_profile_id)
            if step.assigned_agent_profile_id is not None
            else None
        )
        active_runs = [run for run in runs if run.status in ACTIVE_RUN_STATUSES]
        blocked_reasons = _step_blocked_reasons(
            step=step,
            assigned_agent=assigned_agent,
            dependency_state=dependency_state,
            active_runs=active_runs,
        )
        return {
            "task_step_id": step.id,
            "work_package_id": step.work_package_id,
            "title": step.title,
            "description": step.description,
            "status": step.status,
            "order_index": step.order_index,
            "required_role": step.required_role,
            "required_skills": step.required_skills,
            "expected_artifacts": step.expected_artifacts,
            "acceptance_criteria": step.acceptance_criteria,
            "review_policy": step.review_policy,
            "dependencies": dependencies,
            "dependency_state": dependency_state,
            "assigned_agent": _agent_payload(assigned_agent),
            "assignment_status": _assignment_status(step, assigned_agent),
            "runnable": step.status == "queued" and not blocked_reasons,
            "blocked_reasons": blocked_reasons,
            "scheduling": {
                "status": dependencies.get("scheduling_status"),
                "blocked_reason": dependencies.get("blocked_reason"),
                "blocked_resource_keys": dependencies.get("blocked_resource_keys"),
                "priority_score": dependencies.get("priority_score"),
                "scheduled_at": dependencies.get("scheduled_at"),
            },
            "runs": [_run_payload(run) for run in runs],
            "active_run_ids": [run.id for run in active_runs],
            "result_summary": step.result_summary,
        }

    def _steps(self, workspace_id: UUID, task_id: UUID) -> list[TaskStep]:
        return list(
            self._session.scalars(
                select(TaskStep)
                .where(TaskStep.workspace_id == workspace_id, TaskStep.task_id == task_id)
                .order_by(TaskStep.order_index.asc(), TaskStep.created_at.asc())
            )
        )

    def _runs_by_step_id(
        self,
        workspace_id: UUID,
        task_id: UUID,
    ) -> dict[UUID, list[AgentRun]]:
        runs = self._session.scalars(
            select(AgentRun)
            .where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.task_id == task_id,
                AgentRun.task_step_id.is_not(None),
            )
            .order_by(AgentRun.created_at.asc())
        ).all()
        runs_by_step_id: dict[UUID, list[AgentRun]] = defaultdict(list)
        for run in runs:
            if run.task_step_id is not None:
                runs_by_step_id[run.task_step_id].append(run)
        return runs_by_step_id

    def _agent_map(
        self,
        workspace_id: UUID,
        steps: list[TaskStep],
    ) -> dict[UUID, AgentProfile]:
        agent_ids = {
            step.assigned_agent_profile_id
            for step in steps
            if step.assigned_agent_profile_id is not None
        }
        if not agent_ids:
            return {}
        agents = self._session.scalars(
            select(AgentProfile).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.id.in_(agent_ids),
            )
        ).all()
        return {agent.id: agent for agent in agents}

    def _summary(
        self,
        task: Task,
        steps: list[dict[str, object]],
        runs_by_step_id: dict[UUID, list[AgentRun]],
    ) -> dict[str, object]:
        status_counts = Counter(str(step["status"]) for step in steps)
        blocked = [step for step in steps if step["blocked_reasons"]]
        runnable = [step for step in steps if step["runnable"] is True]
        handoff_counts = Counter(
            str(handoff["status"])
            for step in steps
            if isinstance((handoff := step.get("handoff")), dict)
        )
        active_run_count = sum(
            1
            for runs in runs_by_step_id.values()
            for run in runs
            if run.status in ACTIVE_RUN_STATUSES
        )
        return {
            "task_status": task.status,
            "step_counts": dict(sorted(status_counts.items())),
            "total_steps": len(steps),
            "runnable_steps": len(runnable),
            "blocked_steps": len(blocked),
            "active_runs": active_run_count,
            "unassigned_steps": sum(
                1 for step in steps if step["assignment_status"] == "unassigned"
            ),
            "handoff_counts": dict(sorted(handoff_counts.items())),
            "ready_handoffs": handoff_counts.get("ready_for_downstream", 0),
            "blocked_handoffs": handoff_counts.get("downstream_blocked", 0),
            "next_runnable_step_ids": [step["task_step_id"] for step in runnable],
            "blocked_step_ids": [step["task_step_id"] for step in blocked],
        }


def _dependency_state(
    step: TaskStep,
    step_by_id: dict[UUID, TaskStep],
) -> dict[str, object]:
    after_step_ids = _uuid_list_from_dependencies(step.dependencies, "after_step_ids")
    missing_step_ids = [step_id for step_id in after_step_ids if step_id not in step_by_id]
    incomplete_step_ids = [
        step_id
        for step_id in after_step_ids
        if step_id in step_by_id and step_by_id[step_id].status != "completed"
    ]
    return {
        "after_step_ids": after_step_ids,
        "satisfied": not missing_step_ids and not incomplete_step_ids,
        "missing_step_ids": missing_step_ids,
        "incomplete_step_ids": incomplete_step_ids,
    }


def _downstream_map(steps: list[TaskStep]) -> dict[UUID, list[UUID]]:
    step_ids = {step.id for step in steps}
    downstream_by_step_id: dict[UUID, list[UUID]] = {step.id: [] for step in steps}
    for step in steps:
        for upstream_step_id in _uuid_list_from_dependencies(
            step.dependencies,
            "after_step_ids",
        ):
            if upstream_step_id in step_ids:
                downstream_by_step_id[upstream_step_id].append(step.id)
    return downstream_by_step_id


def _handoff_state(
    step_payload: dict[str, object],
    *,
    step_payload_by_id: dict[UUID, dict[str, object]],
    downstream_by_step_id: dict[UUID, list[UUID]],
) -> dict[str, object]:
    step_id = step_payload["task_step_id"]
    if not isinstance(step_id, UUID):
        return {}
    dependency_state = step_payload.get("dependency_state")
    upstream_step_ids = (
        dependency_state.get("after_step_ids", [])
        if isinstance(dependency_state, dict)
        else []
    )
    downstream_step_ids = downstream_by_step_id.get(step_id, [])
    downstream_steps = [
        step_payload_by_id[downstream_step_id]
        for downstream_step_id in downstream_step_ids
        if downstream_step_id in step_payload_by_id
    ]
    blocked_downstream_step_ids = [
        downstream_step["task_step_id"]
        for downstream_step in downstream_steps
        if downstream_step.get("blocked_reasons")
    ]
    completed_downstream_step_ids = [
        downstream_step["task_step_id"]
        for downstream_step in downstream_steps
        if downstream_step.get("status") == "completed"
    ]
    waiting_downstream_step_ids = [
        downstream_step["task_step_id"]
        for downstream_step in downstream_steps
        if downstream_step.get("status") != "completed"
        and not downstream_step.get("blocked_reasons")
    ]
    runnable_downstream_step_ids = [
        downstream_step["task_step_id"]
        for downstream_step in downstream_steps
        if downstream_step.get("runnable") is True
    ]
    status = _handoff_status(
        step_status=str(step_payload.get("status")),
        has_downstream=bool(downstream_steps),
        downstream_steps=downstream_steps,
        blocked_downstream_step_ids=blocked_downstream_step_ids,
        runnable_downstream_step_ids=runnable_downstream_step_ids,
    )
    return {
        "status": status,
        "requires_handoff": bool(downstream_steps),
        "upstream_step_ids": upstream_step_ids,
        "downstream_step_ids": downstream_step_ids,
        "completed_downstream_step_ids": completed_downstream_step_ids,
        "waiting_downstream_step_ids": waiting_downstream_step_ids,
        "blocked_downstream_step_ids": blocked_downstream_step_ids,
        "runnable_downstream_step_ids": runnable_downstream_step_ids,
        "deliverables_expected": step_payload.get("expected_artifacts", []),
        "has_result_summary": step_payload.get("result_summary") is not None,
        "recommended_actions": _handoff_recommended_actions(
            status=status,
            has_result_summary=step_payload.get("result_summary") is not None,
        ),
    }


def _handoff_status(
    *,
    step_status: str,
    has_downstream: bool,
    downstream_steps: list[dict[str, object]],
    blocked_downstream_step_ids: list[object],
    runnable_downstream_step_ids: list[object],
) -> str:
    if step_status != "completed":
        return "source_incomplete" if has_downstream else "no_downstream"
    if not has_downstream:
        return "final_delivery_ready"
    if len(downstream_steps) == sum(
        1 for downstream_step in downstream_steps if downstream_step.get("status") == "completed"
    ):
        return "consumed"
    if blocked_downstream_step_ids:
        return "downstream_blocked"
    if runnable_downstream_step_ids:
        return "ready_for_downstream"
    return "handoff_in_progress"


def _handoff_recommended_actions(
    *,
    status: str,
    has_result_summary: bool,
) -> list[str]:
    if status == "source_incomplete":
        return ["wait_for_source_completion"]
    if status == "ready_for_downstream":
        return ["schedule_downstream_steps"]
    if status == "downstream_blocked":
        return ["inspect_blocked_downstream"]
    if status == "final_delivery_ready":
        actions = ["request_manager_review"]
        if not has_result_summary:
            actions.append("create_correction")
        return actions
    if status == "no_downstream" and not has_result_summary:
        return ["create_correction"]
    return []


def _step_blocked_reasons(
    *,
    step: TaskStep,
    assigned_agent: AgentProfile | None,
    dependency_state: dict[str, object],
    active_runs: list[AgentRun],
) -> list[str]:
    reasons: list[str] = []
    if step.status != "queued":
        if active_runs:
            reasons.append("active_run_exists")
        return reasons
    if step.assigned_agent_profile_id is None:
        reasons.append("agent_unassigned")
    elif assigned_agent is None:
        reasons.append("assigned_agent_missing")
    elif assigned_agent.status != "active":
        reasons.append("assigned_agent_inactive")
    if dependency_state["missing_step_ids"]:
        reasons.append("dependency_missing")
    if dependency_state["incomplete_step_ids"]:
        reasons.append("dependency_incomplete")
    if active_runs:
        reasons.append("active_run_exists")
    dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
    blocked_reason = dependencies.get("blocked_reason")
    if isinstance(blocked_reason, str) and blocked_reason:
        reasons.append(f"scheduler:{blocked_reason}")
    return reasons


def _assignment_status(step: TaskStep, agent: AgentProfile | None) -> str:
    if step.assigned_agent_profile_id is None:
        return "unassigned"
    if agent is None:
        return "missing_agent"
    if agent.status != "active":
        return "inactive_agent"
    return "assigned"


def _agent_payload(agent: AgentProfile | None) -> dict[str, object] | None:
    if agent is None:
        return None
    return {
        "id": agent.id,
        "name": agent.name,
        "role": agent.role,
        "status": agent.status,
    }


def _run_payload(run: AgentRun) -> dict[str, object]:
    return {
        "id": run.id,
        "status": run.status,
        "agent_profile_id": run.agent_profile_id,
        "runtime_id": run.runtime_id,
        "runtime_space_id": run.runtime_space_id,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "error": run.error,
    }


def _uuid_list_from_dependencies(
    dependencies: dict[str, object],
    key: str,
) -> list[UUID]:
    if not isinstance(dependencies, dict):
        return []
    raw_values = dependencies.get(key)
    if not isinstance(raw_values, list):
        return []
    values: list[UUID] = []
    seen: set[UUID] = set()
    for raw_value in raw_values:
        try:
            value = UUID(str(raw_value))
        except (TypeError, ValueError):
            continue
        if value in seen:
            continue
        values.append(value)
        seen.add(value)
    return values
