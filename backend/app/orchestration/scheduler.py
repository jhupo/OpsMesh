from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
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
    RunStatus.WAITING_RUNTIME.value,
    RunStatus.WAITING_APPROVAL.value,
)


@dataclass(frozen=True)
class WorkspaceSchedulerPolicy:
    paused: bool = False
    pause_reason: str | None = None
    max_active_runs: int | None = None
    max_running_tasks: int | None = None
    max_runs_to_start_per_tick: int | None = None
    max_steps_per_task_per_tick: int = 1
    starvation_boost_after_seconds: int | None = None
    resource_limits: dict[str, float] | None = None


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
        if policy.paused:
            reason = policy.pause_reason or "workspace_scheduler_paused"
            self._mark_blocked(candidate_steps, reason)
            return SchedulingDecision(
                (),
                tuple(candidate_steps),
                reason,
                self._available_run_slots(workspace_id, policy),
            )
        task_quota_allowed_steps, task_quota_blocked_steps = self._apply_task_quota(
            workspace_id,
            candidate_steps,
            policy,
        )
        if not task_quota_allowed_steps:
            self._mark_blocked(task_quota_blocked_steps, "workspace_task_quota_exceeded")
            return SchedulingDecision(
                (),
                tuple(task_quota_blocked_steps),
                "workspace_task_quota_exceeded",
                self._available_run_slots(workspace_id, policy),
            )

        available_slots = self._available_run_slots(workspace_id, policy)
        ordered_steps = self._order_steps(task_quota_allowed_steps)
        ordered_steps, resource_blocked_steps = self._apply_resource_limits(
            ordered_steps,
            policy,
        )
        if available_slots is not None:
            available_slots = min(
                available_slots,
                policy.max_runs_to_start_per_tick or available_slots,
            )
            if available_slots <= 0:
                blocked = [*ordered_steps, *resource_blocked_steps, *task_quota_blocked_steps]
                self._mark_blocked(ordered_steps, "workspace_run_quota_exceeded")
                self._mark_blocked(resource_blocked_steps, "workspace_resource_quota_exceeded")
                self._mark_blocked(task_quota_blocked_steps, "workspace_task_quota_exceeded")
                return SchedulingDecision(
                    (),
                    tuple(blocked),
                    "workspace_run_quota_exceeded",
                    0,
                )
            runnable = ordered_steps[:available_slots]
            blocked = [*ordered_steps[available_slots:], *resource_blocked_steps]
        else:
            runnable = (
                ordered_steps[: policy.max_runs_to_start_per_tick]
                if policy.max_runs_to_start_per_tick is not None
                else ordered_steps
            )
            blocked = [*ordered_steps[len(runnable) :], *resource_blocked_steps]

        self._mark_runnable(runnable)
        if blocked:
            run_blocked_steps = [step for step in blocked if step not in resource_blocked_steps]
            if run_blocked_steps:
                self._mark_blocked(run_blocked_steps, "workspace_run_quota_exceeded")
            if resource_blocked_steps:
                self._mark_blocked(resource_blocked_steps, "workspace_resource_quota_exceeded")
        if task_quota_blocked_steps:
            self._mark_blocked(task_quota_blocked_steps, "workspace_task_quota_exceeded")
        all_blocked = [*blocked, *task_quota_blocked_steps]
        return SchedulingDecision(
            runnable_steps=tuple(runnable),
            blocked_steps=tuple(all_blocked),
            blocked_reason=_blocked_reason(
                run_blocked=bool([step for step in blocked if step not in resource_blocked_steps]),
                resource_blocked=bool(resource_blocked_steps),
                task_blocked=bool(task_quota_blocked_steps),
            ),
            available_run_slots=available_slots,
        )

    def _policy_for(self, workspace_id: UUID) -> WorkspaceSchedulerPolicy:
        workspace = self._session.get(Workspace, workspace_id)
        raw_settings = workspace.settings if workspace is not None else {}
        raw_scheduler = raw_settings.get("scheduler") if isinstance(raw_settings, dict) else None
        scheduler = raw_scheduler if isinstance(raw_scheduler, dict) else {}
        return WorkspaceSchedulerPolicy(
            paused=scheduler.get("paused") is True,
            pause_reason=_non_empty_string_or_none(scheduler.get("pause_reason")),
            max_active_runs=_positive_int_or_none(scheduler.get("max_active_runs")),
            max_running_tasks=_positive_int_or_none(scheduler.get("max_running_tasks")),
            max_runs_to_start_per_tick=_positive_int_or_none(
                scheduler.get("max_runs_to_start_per_tick")
            ),
            max_steps_per_task_per_tick=_positive_int_or_default(
                scheduler.get("max_steps_per_task_per_tick"),
                1,
            ),
            starvation_boost_after_seconds=_positive_int_or_none(
                scheduler.get("starvation_boost_after_seconds")
            ),
            resource_limits=_positive_number_dict(scheduler.get("resource_limits")),
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

    def _active_run_task_ids(
        self,
        workspace_id: UUID,
        task_ids: set[UUID] | None,
    ) -> set[UUID]:
        statement = select(AgentRun.task_id).where(
            AgentRun.workspace_id == workspace_id,
            AgentRun.task_id.is_not(None),
            AgentRun.status.in_(ACTIVE_RUN_STATUSES),
        )
        if task_ids is not None:
            if not task_ids:
                return set()
            statement = statement.where(AgentRun.task_id.in_(task_ids))
        return {
            task_id
            for task_id in self._session.scalars(statement).all()
            if task_id is not None
        }

    def _apply_task_quota(
        self,
        workspace_id: UUID,
        candidate_steps: list[TaskStep],
        policy: WorkspaceSchedulerPolicy,
    ) -> tuple[list[TaskStep], list[TaskStep]]:
        if policy.max_running_tasks is None:
            return candidate_steps, []

        candidate_task_ids = {step.task_id for step in candidate_steps}
        already_running_task_ids = self._active_run_task_ids(workspace_id, candidate_task_ids)
        active_task_ids = self._active_run_task_ids(workspace_id, None)
        remaining_new_task_slots = max(policy.max_running_tasks - len(active_task_ids), 0)
        selected_new_task_ids: set[UUID] = set()
        allowed_steps: list[TaskStep] = []
        blocked_steps: list[TaskStep] = []
        for step in self._order_steps(candidate_steps):
            if step.task_id in already_running_task_ids:
                allowed_steps.append(step)
                continue
            if step.task_id in selected_new_task_ids:
                allowed_steps.append(step)
                continue
            if remaining_new_task_slots > 0:
                selected_new_task_ids.add(step.task_id)
                remaining_new_task_slots -= 1
                allowed_steps.append(step)
                continue
            blocked_steps.append(step)
        return allowed_steps, blocked_steps

    def _apply_resource_limits(
        self,
        ordered_steps: list[TaskStep],
        policy: WorkspaceSchedulerPolicy,
    ) -> tuple[list[TaskStep], list[TaskStep]]:
        if not policy.resource_limits:
            return ordered_steps, []
        used = {key: 0.0 for key in policy.resource_limits}
        allowed_steps: list[TaskStep] = []
        blocked_steps: list[TaskStep] = []
        for step in ordered_steps:
            requirements = _step_resource_requirements(step)
            exceeded_keys = [
                key
                for key, limit in policy.resource_limits.items()
                if used[key] + requirements.get(key, 0.0) > limit
            ]
            if exceeded_keys:
                self._set_blocked_resource_keys(step, exceeded_keys)
                blocked_steps.append(step)
                continue
            for key in policy.resource_limits:
                used[key] += requirements.get(key, 0.0)
            allowed_steps.append(step)
        return allowed_steps, blocked_steps


    def _order_steps(self, steps: list[TaskStep]) -> list[TaskStep]:
        policy = self._policy_for(steps[0].workspace_id)
        task_ids = {step.task_id for step in steps}
        task_rank = {
            task_id: _TaskRank(priority=int(priority or 0), created_at=created_at)
            for task_id, priority, created_at in self._session.execute(
                select(Task.id, Task.priority, Task.created_at).where(Task.id.in_(task_ids))
            ).all()
        }
        scored_steps = [
            _ScoredStep(
                step=step,
                rank=task_rank.get(step.task_id, _TaskRank()),
                priority_score=_priority_score(
                    task_rank.get(step.task_id, _TaskRank()).priority,
                    step.created_at,
                    policy.starvation_boost_after_seconds,
                ),
            )
            for step in steps
        ]
        ordered_by_task = sorted(
            scored_steps,
            key=lambda scored: (
                -scored.priority_score,
                scored.rank.created_at,
                scored.step.order_index,
                scored.step.created_at,
                scored.step.id,
            ),
        )
        return self._round_robin_by_task(
            [scored.step for scored in ordered_by_task],
            max_steps_per_task_per_round=policy.max_steps_per_task_per_tick,
        )

    def _round_robin_by_task(
        self,
        steps: Iterable[TaskStep],
        *,
        max_steps_per_task_per_round: int,
    ) -> list[TaskStep]:
        max_per_round = max(1, max_steps_per_task_per_round)
        task_queues: dict[UUID, deque[TaskStep]] = defaultdict(deque)
        task_order: list[UUID] = []
        for step in steps:
            if step.task_id not in task_queues:
                task_order.append(step.task_id)
            task_queues[step.task_id].append(step)

        ordered: list[TaskStep] = []
        active_task_ids = deque(task_order)
        while active_task_ids:
            task_id = active_task_ids.popleft()
            task_steps = task_queues[task_id]
            for _ in range(max_per_round):
                if not task_steps:
                    break
                ordered.append(task_steps.popleft())
            if task_steps:
                active_task_ids.append(task_id)
        return ordered

    def _mark_runnable(self, steps: list[TaskStep]) -> None:
        scheduled_at = datetime.now(UTC).isoformat()
        for step in steps:
            dependencies = dict(step.dependencies) if isinstance(step.dependencies, dict) else {}
            dependencies.pop("scheduling_status", None)
            dependencies.pop("blocked_reason", None)
            dependencies.pop("blocked_resource_keys", None)
            dependencies["scheduled_at"] = scheduled_at
            dependencies["priority_score"] = self._step_priority_score(step)
            step.dependencies = dependencies

    def _mark_blocked(self, steps: list[TaskStep], reason: str) -> None:
        for step in steps:
            dependencies = dict(step.dependencies) if isinstance(step.dependencies, dict) else {}
            dependencies["scheduling_status"] = "blocked"
            dependencies["blocked_reason"] = reason
            dependencies.pop("scheduled_at", None)
            dependencies["priority_score"] = self._step_priority_score(step)
            step.dependencies = dependencies

    def _set_blocked_resource_keys(self, step: TaskStep, keys: list[str]) -> None:
        dependencies = dict(step.dependencies) if isinstance(step.dependencies, dict) else {}
        dependencies["blocked_resource_keys"] = keys
        step.dependencies = dependencies

    def _step_priority_score(self, step: TaskStep) -> int:
        policy = self._policy_for(step.workspace_id)
        task = self._session.get(Task, step.task_id)
        return _priority_score(
            int(task.priority if task is not None else 0),
            step.created_at,
            policy.starvation_boost_after_seconds,
        )


def _positive_int_or_none(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    return None


def _positive_int_or_default(value: object, default: int) -> int:
    parsed = _positive_int_or_none(value)
    return parsed if parsed is not None else default


def _non_empty_string_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _blocked_reason(
    *,
    run_blocked: bool,
    resource_blocked: bool,
    task_blocked: bool,
) -> str | None:
    if run_blocked:
        return "workspace_run_quota_exceeded"
    if resource_blocked:
        return "workspace_resource_quota_exceeded"
    if task_blocked:
        return "workspace_task_quota_exceeded"
    return None


@dataclass(frozen=True)
class _TaskRank:
    priority: int = 0
    created_at: datetime = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True)
class _ScoredStep:
    step: TaskStep
    rank: _TaskRank
    priority_score: int


def _priority_score(
    priority: int,
    created_at: datetime | None,
    starvation_boost_after_seconds: int | None,
) -> int:
    if starvation_boost_after_seconds is None or created_at is None:
        return priority
    elapsed_seconds = max(0, int((datetime.now(UTC) - _aware_datetime(created_at)).total_seconds()))
    return priority + elapsed_seconds // starvation_boost_after_seconds


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _positive_number_dict(value: object) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    result = {
        str(key): float(raw_value)
        for key, raw_value in value.items()
        if isinstance(raw_value, int | float) and raw_value >= 0
    }
    return result or None


def _step_resource_requirements(step: TaskStep) -> dict[str, float]:
    dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
    raw_requirements = dependencies.get("resource_requirements")
    if not isinstance(raw_requirements, dict):
        return {}
    return {
        str(key): float(raw_value)
        for key, raw_value in raw_requirements.items()
        if isinstance(raw_value, int | float) and raw_value > 0
    }
