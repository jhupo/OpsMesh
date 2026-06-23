from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.orchestration.scheduler_policy import WorkspaceSchedulerPolicy
from backend.app.tasks.models import Task, TaskStep


class SchedulerStepOrdering:
    def __init__(self, session: Session) -> None:
        self._session = session

    def order_steps(
        self,
        steps: list[TaskStep],
        *,
        policy: WorkspaceSchedulerPolicy,
    ) -> list[TaskStep]:
        task_ids = {step.task_id for step in steps}
        task_rank = {
            task_id: TaskRank(priority=int(priority or 0), created_at=created_at)
            for task_id, priority, created_at in self._session.execute(
                select(Task.id, Task.priority, Task.created_at).where(Task.id.in_(task_ids))
            ).all()
        }
        scored_steps = [
            ScoredStep(
                step=step,
                rank=task_rank.get(step.task_id, TaskRank()),
                priority_score=priority_score(
                    task_rank.get(step.task_id, TaskRank()).priority,
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
        return round_robin_by_task(
            [scored.step for scored in ordered_by_task],
            max_steps_per_task_per_round=policy.max_steps_per_task_per_tick,
        )

    def step_priority_score(
        self,
        step: TaskStep,
        *,
        policy: WorkspaceSchedulerPolicy,
    ) -> int:
        task = self._session.get(Task, step.task_id)
        return priority_score(
            int(task.priority if task is not None else 0),
            step.created_at,
            policy.starvation_boost_after_seconds,
        )


def round_robin_by_task(
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


def priority_score(
    priority: int,
    created_at: datetime | None,
    starvation_boost_after_seconds: int | None,
) -> int:
    if starvation_boost_after_seconds is None or created_at is None:
        return priority
    elapsed_seconds = max(0, int((datetime.now(UTC) - aware_datetime(created_at)).total_seconds()))
    return priority + elapsed_seconds // starvation_boost_after_seconds


def aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@dataclass(frozen=True)
class TaskRank:
    priority: int = 0
    created_at: datetime = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True)
class ScoredStep:
    step: TaskStep
    rank: TaskRank
    priority_score: int
