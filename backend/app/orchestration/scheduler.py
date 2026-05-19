from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.tasks.models import Task, TaskStep
from backend.app.workspaces.models import Workspace

ACTIVE_RUN_STATUSES = (
    RunStatus.QUEUED.value,
    RunStatus.RUNNING.value,
    RunStatus.WAITING_APPROVAL.value,
)
ACTIVE_TASK_STATUSES = ("queued", "running", "waiting_approval")


@dataclass(frozen=True)
class WorkspaceSchedulerPolicy:
    max_active_runs: int | None = None
    max_running_tasks: int | None = None
    max_runs_to_start_per_tick: int | None = None


@dataclass(frozen=True)
class SchedulingDecision:
    runnable_steps: tuple[TaskStep, ...]
    blocked_steps: tuple[TaskStep, ...]
    blocked_reason: str | None
    available_run_slots: int | None


class WorkspaceScheduler:
    def __init__(self, session: Session) -> None:
        self._session = session

    def select_runnable_steps(
        self,
        *,
        workspace_id: UUID,
        candidate_steps: list[TaskStep],
    ) -> SchedulingDecision:
        if not candidate_steps:
            return SchedulingDecision((), (), None, None)
        policy = self._policy_for(workspace_id)
        if policy.max_running_tasks is not None:
            running_tasks = self._active_task_count(workspace_id)
            candidate_task_ids = {step.task_id for step in candidate_steps}
            already_running_candidate_tasks = self._active_task_ids(
                workspace_id,
                candidate_task_ids,
            )
            new_task_count = len(candidate_task_ids - already_running_candidate_tasks)
            if running_tasks + new_task_count > policy.max_running_tasks:
                self._mark_blocked(candidate_steps, "workspace_task_quota_exceeded")
                return SchedulingDecision(
                    (),
                    tuple(candidate_steps),
                    "workspace_task_quota_exceeded",
                    self._available_run_slots(workspace_id, policy),
                )

        available_slots = self._available_run_slots(workspace_id, policy)
        ordered_steps = self._order_steps(candidate_steps)
        if available_slots is not None:
            available_slots = min(
                available_slots,
                policy.max_runs_to_start_per_tick or available_slots,
            )
            if available_slots <= 0:
                self._mark_blocked(ordered_steps, "workspace_run_quota_exceeded")
                return SchedulingDecision(
                    (),
                    tuple(ordered_steps),
                    "workspace_run_quota_exceeded",
                    0,
                )
            runnable = ordered_steps[:available_slots]
            blocked = ordered_steps[available_slots:]
        else:
            runnable = (
                ordered_steps[: policy.max_runs_to_start_per_tick]
                if policy.max_runs_to_start_per_tick is not None
                else ordered_steps
            )
            blocked = ordered_steps[len(runnable) :]

        self._mark_runnable(runnable)
        if blocked:
            self._mark_blocked(blocked, "workspace_run_quota_exceeded")
        return SchedulingDecision(
            runnable_steps=tuple(runnable),
            blocked_steps=tuple(blocked),
            blocked_reason="workspace_run_quota_exceeded" if blocked else None,
            available_run_slots=available_slots,
        )

    def _policy_for(self, workspace_id: UUID) -> WorkspaceSchedulerPolicy:
        workspace = self._session.get(Workspace, workspace_id)
        raw_settings = workspace.settings if workspace is not None else {}
        raw_scheduler = raw_settings.get("scheduler") if isinstance(raw_settings, dict) else None
        scheduler = raw_scheduler if isinstance(raw_scheduler, dict) else {}
        return WorkspaceSchedulerPolicy(
            max_active_runs=_positive_int_or_none(scheduler.get("max_active_runs")),
            max_running_tasks=_positive_int_or_none(scheduler.get("max_running_tasks")),
            max_runs_to_start_per_tick=_positive_int_or_none(
                scheduler.get("max_runs_to_start_per_tick")
            ),
        )

    def _available_run_slots(
        self,
        workspace_id: UUID,
        policy: WorkspaceSchedulerPolicy,
    ) -> int | None:
        if policy.max_active_runs is None:
            return None
        active_runs = self._session.scalar(
            select(func.count(AgentRun.id)).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.status.in_(ACTIVE_RUN_STATUSES),
            )
        )
        return max(policy.max_active_runs - int(active_runs or 0), 0)

    def _active_task_count(self, workspace_id: UUID) -> int:
        return int(
            self._session.scalar(
                select(func.count(Task.id)).where(
                    Task.workspace_id == workspace_id,
                    Task.status.in_(ACTIVE_TASK_STATUSES),
                )
            )
            or 0
        )

    def _active_task_ids(self, workspace_id: UUID, task_ids: set[UUID]) -> set[UUID]:
        if not task_ids:
            return set()
        return set(
            self._session.scalars(
                select(Task.id).where(
                    Task.workspace_id == workspace_id,
                    Task.id.in_(task_ids),
                    Task.status.in_(ACTIVE_TASK_STATUSES),
                )
            ).all()
        )

    def _order_steps(self, steps: list[TaskStep]) -> list[TaskStep]:
        task_ids = {step.task_id for step in steps}
        priorities = {
            task_id: priority
            for task_id, priority in self._session.execute(
                select(Task.id, Task.priority).where(Task.id.in_(task_ids))
            ).all()
        }
        return sorted(
            steps,
            key=lambda step: (
                -int(priorities.get(step.task_id, 0) or 0),
                step.order_index,
                step.created_at,
                step.id,
            ),
        )

    def _mark_runnable(self, steps: list[TaskStep]) -> None:
        for step in steps:
            dependencies = dict(step.dependencies) if isinstance(step.dependencies, dict) else {}
            dependencies.pop("scheduling_status", None)
            dependencies.pop("blocked_reason", None)
            step.dependencies = dependencies

    def _mark_blocked(self, steps: list[TaskStep], reason: str) -> None:
        for step in steps:
            dependencies = dict(step.dependencies) if isinstance(step.dependencies, dict) else {}
            dependencies["scheduling_status"] = "blocked"
            dependencies["blocked_reason"] = reason
            step.dependencies = dependencies


def _positive_int_or_none(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    return None
