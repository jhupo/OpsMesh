from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.orchestration.policies.statuses import ACTIVE_RUN_STATUS_VALUES
from backend.app.orchestration.scheduler.execution_limits import SchedulerExecutionLimiter
from backend.app.orchestration.scheduler.ordering import SchedulerStepOrdering
from backend.app.orchestration.scheduler.policy import (
    SchedulerPolicyResolver,
    WorkspaceSchedulerPolicy,
)
from backend.app.orchestration.steps.scheduling_state import (
    mark_step_scheduling_blocked,
    mark_step_scheduling_runnable,
)
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import TaskStep

ACTIVE_RUN_STATUSES = ACTIVE_RUN_STATUS_VALUES


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
        policy_override: dict[str, object] | None = None,
    ) -> SchedulingDecision:
        if not candidate_steps:
            return SchedulingDecision((), (), None, None)
        policy = SchedulerPolicyResolver(self._session).policy_for(
            workspace_id,
            override=policy_override,
        )
        if policy.paused:
            reason = policy.pause_reason or "workspace_scheduler_paused"
            self._mark_blocked(candidate_steps, reason, policy=policy)
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
            self._mark_blocked(
                task_quota_blocked_steps,
                "workspace_task_quota_exceeded",
                policy=policy,
            )
            return SchedulingDecision(
                (),
                tuple(task_quota_blocked_steps),
                "workspace_task_quota_exceeded",
                self._available_run_slots(workspace_id, policy),
            )

        available_slots = self._available_run_slots(workspace_id, policy)
        ordered_steps = self._ordering().order_steps(task_quota_allowed_steps, policy=policy)
        limit_result = SchedulerExecutionLimiter(self._session).apply(
            ordered_steps,
            policy,
        )
        ordered_steps = limit_result.allowed_steps
        resource_blocked_steps = limit_result.resource_blocked_steps
        member_blocked_steps_by_reason = limit_result.member_blocked_steps_by_reason
        member_blocked_steps = [
            step for steps in member_blocked_steps_by_reason.values() for step in steps
        ]
        member_blocked_ids = {step.id for step in member_blocked_steps}
        resource_blocked_ids = {step.id for step in resource_blocked_steps}
        if not ordered_steps and (resource_blocked_steps or member_blocked_steps):
            self._mark_blocked(
                resource_blocked_steps,
                "workspace_resource_quota_exceeded",
                policy=policy,
            )
            self._mark_member_blocked(member_blocked_steps_by_reason, policy=policy)
            if task_quota_blocked_steps:
                self._mark_blocked(
                    task_quota_blocked_steps,
                    "workspace_task_quota_exceeded",
                    policy=policy,
                )
            return SchedulingDecision(
                (),
                tuple([*resource_blocked_steps, *member_blocked_steps, *task_quota_blocked_steps]),
                _blocked_reason(
                    run_blocked=False,
                    resource_blocked=bool(resource_blocked_steps),
                    member_blocked=bool(member_blocked_steps),
                    task_blocked=bool(task_quota_blocked_steps),
                    member_blocked_reason=_first_blocked_reason(
                        member_blocked_steps_by_reason,
                    ),
                ),
                available_slots,
            )
        if available_slots is not None:
            available_slots = min(
                available_slots,
                policy.max_runs_to_start_per_tick or available_slots,
            )
            if available_slots <= 0:
                blocked = [
                    *ordered_steps,
                    *resource_blocked_steps,
                    *member_blocked_steps,
                    *task_quota_blocked_steps,
                ]
                self._mark_blocked(ordered_steps, "workspace_run_quota_exceeded", policy=policy)
                self._mark_blocked(
                    resource_blocked_steps,
                    "workspace_resource_quota_exceeded",
                    policy=policy,
                )
                self._mark_member_blocked(member_blocked_steps_by_reason, policy=policy)
                self._mark_blocked(
                    task_quota_blocked_steps,
                    "workspace_task_quota_exceeded",
                    policy=policy,
                )
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
        blocked = [*blocked, *member_blocked_steps]

        self._mark_runnable(runnable, policy=policy)
        if blocked:
            run_blocked_steps = [
                step
                for step in blocked
                if step.id not in resource_blocked_ids and step.id not in member_blocked_ids
            ]
            if run_blocked_steps:
                self._mark_blocked(
                    run_blocked_steps,
                    "workspace_run_quota_exceeded",
                    policy=policy,
                )
            if resource_blocked_steps:
                self._mark_blocked(
                    resource_blocked_steps,
                    "workspace_resource_quota_exceeded",
                    policy=policy,
                )
            self._mark_member_blocked(member_blocked_steps_by_reason, policy=policy)
        if task_quota_blocked_steps:
            self._mark_blocked(
                task_quota_blocked_steps,
                "workspace_task_quota_exceeded",
                policy=policy,
            )
        all_blocked = [*blocked, *task_quota_blocked_steps]
        return SchedulingDecision(
            runnable_steps=tuple(runnable),
            blocked_steps=tuple(all_blocked),
            blocked_reason=_blocked_reason(
                run_blocked=bool(
                    [
                        step
                        for step in blocked
                        if step.id not in resource_blocked_ids and step.id not in member_blocked_ids
                    ]
                ),
                resource_blocked=bool(resource_blocked_steps),
                member_blocked=bool(member_blocked_steps),
                task_blocked=bool(task_quota_blocked_steps),
                member_blocked_reason=_first_blocked_reason(member_blocked_steps_by_reason),
            ),
            available_run_slots=available_slots,
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
            task_id for task_id in self._session.scalars(statement).all() if task_id is not None
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
        for step in self._ordering().order_steps(candidate_steps, policy=policy):
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

    def _mark_runnable(
        self,
        steps: list[TaskStep],
        *,
        policy: WorkspaceSchedulerPolicy,
    ) -> None:
        scheduled_at = datetime.now(UTC).isoformat()
        for step in steps:
            mark_step_scheduling_runnable(
                step,
                scheduled_at=scheduled_at,
                priority_score=self._ordering().step_priority_score(step, policy=policy),
            )

    def _mark_blocked(
        self,
        steps: list[TaskStep],
        reason: str,
        *,
        policy: WorkspaceSchedulerPolicy,
    ) -> None:
        for step in steps:
            mark_step_scheduling_blocked(
                step,
                reason,
                priority_score=self._ordering().step_priority_score(step, policy=policy),
            )

    def _mark_member_blocked(
        self,
        steps_by_reason: dict[str, list[TaskStep]],
        *,
        policy: WorkspaceSchedulerPolicy,
    ) -> None:
        for reason, steps in steps_by_reason.items():
            self._mark_blocked(steps, reason, policy=policy)

    def _ordering(self) -> SchedulerStepOrdering:
        return SchedulerStepOrdering(self._session)


def _blocked_reason(
    *,
    run_blocked: bool,
    resource_blocked: bool,
    member_blocked: bool,
    task_blocked: bool,
    member_blocked_reason: str | None,
) -> str | None:
    if run_blocked:
        return "workspace_run_quota_exceeded"
    if resource_blocked:
        return "workspace_resource_quota_exceeded"
    if member_blocked:
        return member_blocked_reason or "team_member_capacity_exceeded"
    if task_blocked:
        return "workspace_task_quota_exceeded"
    return None


def _first_blocked_reason(steps_by_reason: dict[str, list[TaskStep]]) -> str | None:
    return next(iter(steps_by_reason), None)
