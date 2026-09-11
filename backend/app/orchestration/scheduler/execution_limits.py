from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.orchestration.scheduler.policy import WorkspaceSchedulerPolicy
from backend.app.orchestration.scheduler.team_capacity import (
    TeamMemberCapacityResolver,
    member_blocked_reason,
)
from backend.app.orchestration.state.resource_usage import step_resource_requirements
from backend.app.orchestration.steps.scheduling_state import set_blocked_resource_keys
from backend.app.tasks.models import TaskStep


@dataclass(frozen=True)
class ExecutionLimitResult:
    allowed_steps: list[TaskStep]
    resource_blocked_steps: list[TaskStep]
    member_blocked_steps_by_reason: dict[str, list[TaskStep]]


class SchedulerExecutionLimiter:
    def __init__(self, session: Session) -> None:
        self._session = session

    def apply(
        self,
        ordered_steps: list[TaskStep],
        policy: WorkspaceSchedulerPolicy,
    ) -> ExecutionLimitResult:
        resource_limits = policy.resource_limits or {}
        used = {key: 0.0 for key in resource_limits}
        member_contexts = TeamMemberCapacityResolver(self._session).contexts(ordered_steps)
        selected_task_ids_by_agent: dict[UUID, set[UUID]] = defaultdict(set)
        allowed_steps: list[TaskStep] = []
        resource_blocked_steps: list[TaskStep] = []
        member_blocked_steps_by_reason: dict[str, list[TaskStep]] = defaultdict(list)

        for step in ordered_steps:
            member_reason = member_blocked_reason(
                step,
                member_contexts.get(step.id),
                selected_task_ids_by_agent,
            )
            if member_reason is not None:
                member_blocked_steps_by_reason[member_reason].append(step)
                continue

            requirements = step_resource_requirements(step)
            exceeded_keys = [
                key
                for key, limit in resource_limits.items()
                if used[key] + requirements.get(key, 0.0) > limit
            ]
            if exceeded_keys:
                set_blocked_resource_keys(step, exceeded_keys)
                resource_blocked_steps.append(step)
                continue

            for key in resource_limits:
                used[key] += requirements.get(key, 0.0)
            context = member_contexts.get(step.id)
            if (
                context is not None
                and not isinstance(context, str)
                and step.task_id not in context.active_task_ids
            ):
                selected_task_ids_by_agent[context.agent_profile_id].add(step.task_id)
            allowed_steps.append(step)

        return ExecutionLimitResult(
            allowed_steps=allowed_steps,
            resource_blocked_steps=resource_blocked_steps,
            member_blocked_steps_by_reason=dict(member_blocked_steps_by_reason),
        )
