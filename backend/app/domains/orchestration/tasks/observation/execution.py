from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security.redaction import redact_sensitive_payload
from backend.app.core.utils import dict_list
from backend.app.domains.agents.profiles.models import AgentProfile
from backend.app.domains.orchestration.approvals.models import Approval, PendingToolInvocation
from backend.app.domains.orchestration.models import SubworkflowInvocation
from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.domains.orchestration.runs.state import RunStatus
from backend.app.domains.orchestration.tasks.collaboration.handoff import (
    handoff_needs_attention,
    handoff_queue_item,
    handoff_queue_summary,
)
from backend.app.domains.orchestration.tasks.models import Task, TaskStep
from backend.app.domains.orchestration.tasks.observation.execution_views import (
    build_step_payload,
    downstream_map,
    handoff_state,
)
from backend.app.domains.workspace.teams.models import AgentTeam

ACTIVE_RUN_STATUSES = {
    RunStatus.QUEUED.value,
    RunStatus.RUNNING.value,
    RunStatus.WAITING_RUNTIME.value,
    RunStatus.WAITING_APPROVAL.value,
    RunStatus.WAITING_SUBWORKFLOW.value,
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
        approvals_by_run_id, pending_by_approval_id = self._approval_state(
            workspace_id,
            task.id,
        )
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
            step.id: payload for step, payload in zip(steps, step_payloads, strict=True)
        }
        invocations = self._session.scalars(
            select(SubworkflowInvocation).where(
                SubworkflowInvocation.workspace_id == workspace_id,
                SubworkflowInvocation.parent_task_id == task.id,
            )
        ).all()
        invocation_by_step = {
            invocation.parent_task_step_id: invocation for invocation in invocations
        }
        for step_payload in step_payloads:
            step_id = step_payload.get("task_step_id")
            runs = runs_by_step_id.get(step_id, []) if isinstance(step_id, UUID) else []
            approvals = [
                approval
                for run in runs
                for approval in approvals_by_run_id.get(run.id, [])
            ]
            step_payload["attempt_count"] = len(runs)
            step_payload["approval_state"] = _approval_state_payload(
                approvals,
                pending_by_approval_id,
            )
            step_payload["diagnostics"] = _step_diagnostics(runs, approvals)
            invocation = invocation_by_step.get(step_id) if isinstance(step_id, UUID) else None
            if invocation is not None:
                step_payload["subworkflow"] = {
                    "invocation_id": invocation.id,
                    "child_task_id": invocation.child_task_id,
                    "definition_id": invocation.definition_id,
                    "definition_version": invocation.definition_version,
                    "status": invocation.status,
                    "output": invocation.output_payload,
                    "error": invocation.error_payload,
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
            team = self._session.scalar(
                select(AgentTeam).where(
                    AgentTeam.workspace_id == workspace_id, AgentTeam.id == team_id
                )
            )
            if team is None:
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

    def _approval_state(
        self,
        workspace_id: UUID,
        task_id: UUID,
    ) -> tuple[dict[UUID, list[Approval]], dict[UUID, list[PendingToolInvocation]]]:
        approvals = self._session.scalars(
            select(Approval)
            .where(
                Approval.workspace_id == workspace_id,
                Approval.task_id == task_id,
            )
            .order_by(Approval.created_at.asc())
        ).all()
        approval_by_run_id: dict[UUID, list[Approval]] = defaultdict(list)
        approval_ids = {approval.id for approval in approvals}
        for approval in approvals:
            if approval.agent_run_id is not None:
                approval_by_run_id[approval.agent_run_id].append(approval)
        pending_by_approval_id: dict[UUID, list[PendingToolInvocation]] = defaultdict(list)
        if approval_ids:
            pending = self._session.scalars(
                select(PendingToolInvocation)
                .where(
                    PendingToolInvocation.workspace_id == workspace_id,
                    PendingToolInvocation.approval_id.in_(approval_ids),
                )
                .order_by(PendingToolInvocation.created_at.asc())
            ).all()
            for invocation in pending:
                pending_by_approval_id[invocation.approval_id].append(invocation)
        return approval_by_run_id, pending_by_approval_id

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
        pending_approval_steps = sum(
            1
            for step in steps
            if _has_pending_approval(step)
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
            "pending_approval_steps": pending_approval_steps,
            "failed_runs": sum(
                1
                for runs in runs_by_step_id.values()
                for run in runs
                if run.status == RunStatus.FAILED.value
            ),
        }


def _approval_state_payload(
    approvals: list[Approval],
    pending_by_approval_id: dict[UUID, list[PendingToolInvocation]],
) -> dict[str, object]:
    statuses = Counter(approval.status for approval in approvals)
    pending_invocations = [
        invocation
        for approval in approvals
        for invocation in pending_by_approval_id.get(approval.id, [])
    ]
    return {
        "count": len(approvals),
        "pending_count": statuses.get("pending", 0),
        "approved_count": statuses.get("approved", 0),
        "rejected_count": statuses.get("rejected", 0),
        "statuses": dict(sorted(statuses.items())),
        "pending_tool_invocations": [
            {
                "id": invocation.id,
                "tool_name": invocation.tool_name,
                "tool_kind": invocation.tool_kind,
                "status": invocation.status,
                "attempt_count": invocation.attempt_count,
            }
            for invocation in pending_invocations
        ],
        "latest": (
            {
                "id": approvals[-1].id,
                "status": approvals[-1].status,
                "approval_type": approvals[-1].approval_type,
                "risk_level": approvals[-1].risk_level,
                "created_at": approvals[-1].created_at,
                "decided_at": approvals[-1].decided_at,
                "decision_reason": approvals[-1].decision_reason,
            }
            if approvals
            else None
        ),
    }


def _step_diagnostics(
    runs: list[AgentRun],
    approvals: list[Approval],
) -> dict[str, object]:
    latest = runs[-1] if runs else None
    return {
        "latest_run_status": latest.status if latest is not None else None,
        "latest_run_id": latest.id if latest is not None else None,
        "failure_count": sum(1 for run in runs if run.status == RunStatus.FAILED.value),
        "approval_required": bool(approvals),
        "approval_blocked": any(approval.status == "pending" for approval in approvals),
        "last_error": (
            redact_sensitive_payload(latest.error)
            if latest is not None and isinstance(latest.error, dict)
            else None
        ),
    }


def _has_pending_approval(step: dict[str, object]) -> bool:
    approval_state = step.get("approval_state")
    if not isinstance(approval_state, dict):
        return False
    pending_count = approval_state.get("pending_count")
    return isinstance(pending_count, int) and pending_count > 0
