from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.pagination import PageParams
from backend.app.core.utils import (
    ensure_aware_utc,
    non_empty_string_or_none,
    positive_int_or_none,
    string_list,
)
from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.domains.orchestration.tasks.models import Task, TaskStep
from backend.app.domains.orchestration.workflows.definitions.blocked_reasons import (
    explain_blocked_reason,
)
from backend.app.domains.orchestration.workflows.statuses import ACTIVE_RUN_STATUS_VALUES
from backend.app.domains.workspace.tenants.models import Workspace
from backend.app.domains.workspace.tenants.settings import scheduler_settings
from backend.app.runtime.operations.contracts.scheduler import (
    BlockedStepExplanationResponse,
    OperationsSchedulerResponse,
    SchedulerBacklogResponse,
    SchedulerBlockedReasonResponse,
    SchedulerPolicyResponse,
    SchedulerPriorityBucketResponse,
)


class SchedulerPolicyService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def scheduler_policy(self, workspace_id: UUID) -> SchedulerPolicyResponse:
        workspace = self._session.get(Workspace, workspace_id)
        scheduler = scheduler_settings(workspace.settings if workspace is not None else {})
        return SchedulerPolicyResponse(
            paused=scheduler.get("paused") is True,
            pause_reason=non_empty_string_or_none(scheduler.get("pause_reason")),
            max_active_runs=positive_int_or_none(scheduler.get("max_active_runs")),
            max_running_tasks=positive_int_or_none(scheduler.get("max_running_tasks")),
            max_runs_to_start_per_tick=positive_int_or_none(
                scheduler.get("max_runs_to_start_per_tick")
            ),
            max_steps_per_task_per_tick=positive_int_or_none(
                scheduler.get("max_steps_per_task_per_tick")
            ),
            starvation_boost_after_seconds=positive_int_or_none(
                scheduler.get("starvation_boost_after_seconds")
            ),
            resource_limits=positive_number_dict(scheduler.get("resource_limits")),
        )


def positive_number_dict(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): float(item)
        for key, item in value.items()
        if isinstance(item, int | float) and not isinstance(item, bool) and item > 0
    }


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
        queued_steps = running_steps = blocked_steps = 0
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
                reason = str(dependencies.get("blocked_reason") or "unknown")
                blocked_reasons[reason] = blocked_reasons.get(reason, 0) + 1
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
                .where(Task.workspace_id == workspace_id, Task.status == "waiting_approval")
            )
            or 0
        )


class SchedulerBlockedStepService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_blocked_steps(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        code: str | None = None,
    ) -> tuple[list[BlockedStepExplanationResponse], int]:
        blocked: list[BlockedStepExplanationResponse] = []
        for step, task in self._blocked_step_rows(workspace_id):
            dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
            if dependencies.get("scheduling_status") != "blocked":
                continue
            explanation = explain_blocked_reason(dependencies.get("blocked_reason"))
            if code is not None and explanation.code != code:
                continue
            blocked.append(
                BlockedStepExplanationResponse(
                    task_step_id=step.id,
                    task_id=task.id,
                    task_title=task.title,
                    step_title=step.title,
                    status=step.status,
                    reason=explanation.reason,
                    code=explanation.code,
                    message=explanation.message,
                    resource_key=explanation.resource_key,
                    runtime_space_id=step.runtime_space_id or task.runtime_space_id,
                    blocked_resource_keys=string_list(dependencies.get("blocked_resource_keys")),
                    priority_score=positive_int_or_none(dependencies.get("priority_score")),
                    created_at=step.created_at,
                    updated_at=step.updated_at,
                )
            )
        return blocked[page.offset : page.offset + page.limit], len(blocked)

    def _blocked_step_rows(self, workspace_id: UUID) -> Sequence[tuple[TaskStep, Task]]:
        return (
            self._session.execute(
                select(TaskStep, Task)
                .join(Task, Task.id == TaskStep.task_id)
                .where(
                    TaskStep.workspace_id == workspace_id,
                    Task.workspace_id == workspace_id,
                    TaskStep.status == "queued",
                )
                .order_by(TaskStep.created_at.asc(), TaskStep.id.asc())
            )
            .tuples()
            .all()
        )
