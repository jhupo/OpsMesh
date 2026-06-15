from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.core.typing import dict_list
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.tasks.execution_handoff import (
    handoff_needs_attention,
    handoff_queue_item,
    handoff_queue_summary,
)
from backend.app.tasks.execution_payloads import build_step_payload, downstream_map, handoff_state
from backend.app.tasks.models import Task, TaskStep
from backend.app.teams.models import AgentTeam

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
        downstream_by_step_id = downstream_map(steps)
        step_payloads = [
            build_step_payload(
                step,
                agents=agents,
                step_by_id=step_by_id,
                runs=runs_by_step_id.get(step.id, []),
                active_run_statuses=ACTIVE_RUN_STATUSES,
            )
            for step in steps
        ]
        step_payload_by_id = {
            step_payload["task_step_id"]: step_payload for step_payload in step_payloads
        }
        for step_payload in step_payloads:
            step_payload["handoff"] = handoff_state(
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

    def list_handoff_queue(
        self,
        *,
        workspace_id: UUID,
        limit: int,
        offset: int,
        task_status: str | None = None,
        team_id: UUID | None = None,
        handoff_status: str | None = None,
        include_terminal: bool = False,
    ) -> dict[str, object]:
        statement = select(Task).where(Task.workspace_id == workspace_id)
        if team_id is not None:
            team = self._session.get(AgentTeam, team_id)
            if team is None or team.workspace_id != workspace_id:
                raise ValueError("Team not found")
            statement = statement.where(Task.agent_team_id == team_id)
        if task_status is not None:
            statement = statement.where(Task.status == task_status)

        tasks = list(
            self._session.scalars(
                statement.order_by(
                    Task.priority.desc(),
                    Task.updated_at.desc(),
                    Task.created_at.desc(),
                )
            )
        )
        items: list[dict[str, object]] = []
        for task in tasks:
            diagnostics = self.get_diagnostics(workspace_id=workspace_id, task_id=task.id)
            if diagnostics is None:
                continue
            for step_payload in dict_list(diagnostics.get("steps")):
                item = handoff_queue_item(task, step_payload)
                if item is None:
                    continue
                if handoff_status is not None and item["handoff_status"] != handoff_status:
                    continue
                if not include_terminal and not handoff_needs_attention(item):
                    continue
                items.append(item)

        total = len(items)
        paged_items = items[offset : offset + limit]
        return {
            "workspace_id": workspace_id,
            "team_id": team_id,
            "generated_at": datetime.now(UTC),
            "total": total,
            "limit": limit,
            "offset": offset,
            "summary": handoff_queue_summary(items),
            "items": paged_items,
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
