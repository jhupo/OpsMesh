from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.schemas.operations import (
    OperationsSchedulerResponse,
    SchedulerBacklogResponse,
    SchedulerBlockedReasonResponse,
    SchedulerPriorityBucketResponse,
)
from backend.app.operations.scheduler_policy import SchedulerPolicyService
from backend.app.operations.utils import ensure_aware_utc
from backend.app.orchestration.policies.blocked_reasons import explain_blocked_reason
from backend.app.orchestration.policies.statuses import ACTIVE_RUN_STATUS_VALUES
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task, TaskStep


class SchedulerBacklogService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def scheduler_payload(self, workspace_id: UUID) -> OperationsSchedulerResponse:
        task_steps = self._session.execute(
            select(TaskStep, Task)
            .join(Task, Task.id == TaskStep.task_id)
            .where(
                TaskStep.workspace_id == workspace_id,
                Task.workspace_id == workspace_id,
                Task.status.in_(["queued", "running", "waiting_approval", "blocked"]),
            )
        ).all()
        now = datetime.now(UTC)
        priority_buckets: dict[int, dict[str, int]] = {}
        blocked_reasons: dict[str, int] = {}
        queued_ages: list[int] = []
        queued_steps = 0
        running_steps = 0
        blocked_steps = 0
        highest_priority: int | None = None
        for step, task in task_steps:
            priority = int(task.priority or 0)
            highest_priority = (
                priority if highest_priority is None else max(highest_priority, priority)
            )
            bucket = priority_buckets.setdefault(
                priority,
                {"queued_steps": 0, "running_steps": 0, "blocked_steps": 0},
            )
            if step.status == "queued":
                queued_steps += 1
                bucket["queued_steps"] += 1
                queued_ages.append(
                    max(0, int((now - ensure_aware_utc(step.created_at)).total_seconds()))
                )
            elif step.status == "running":
                running_steps += 1
                bucket["running_steps"] += 1
            dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
            if dependencies.get("scheduling_status") == "blocked":
                blocked_steps += 1
                bucket["blocked_steps"] += 1
                reason = dependencies.get("blocked_reason")
                blocked_reasons[str(reason or "unknown")] = (
                    blocked_reasons.get(str(reason or "unknown"), 0) + 1
                )
        return OperationsSchedulerResponse(
            generated_at=now,
            backlog=SchedulerBacklogResponse(
                queued_steps=queued_steps,
                running_steps=running_steps,
                waiting_approval_tasks=self._waiting_approval_tasks(workspace_id),
                blocked_steps=blocked_steps,
                active_runs=self._active_runs(workspace_id),
                oldest_queued_age_seconds=max(queued_ages) if queued_ages else None,
                highest_priority=highest_priority,
            ),
            priority_buckets=[
                SchedulerPriorityBucketResponse(priority=priority, **counts)
                for priority, counts in sorted(priority_buckets.items(), reverse=True)
            ],
            blocked_reasons=[
                SchedulerBlockedReasonResponse(
                    reason=explanation.reason,
                    code=explanation.code,
                    message=explanation.message,
                    resource_key=explanation.resource_key,
                    count=count,
                )
                for reason, count in sorted(blocked_reasons.items())
                for explanation in [explain_blocked_reason(reason)]
            ],
            policy=SchedulerPolicyService(self._session).scheduler_policy(workspace_id),
        )

    def _active_runs(self, workspace_id: UUID) -> int:
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(AgentRun)
                .where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.status.in_(ACTIVE_RUN_STATUS_VALUES),
                )
            )
            or 0
        )

    def _waiting_approval_tasks(self, workspace_id: UUID) -> int:
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(Task)
                .where(
                    Task.workspace_id == workspace_id,
                    Task.status == "waiting_approval",
                )
            )
            or 0
        )
