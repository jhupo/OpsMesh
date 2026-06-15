from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.api.schemas.operations import (
    BlockedStepExplanationResponse,
    BlockedStepUnblockResponse,
    OperationsSchedulerResponse,
    SchedulerBacklogResponse,
    SchedulerBlockedReasonResponse,
    SchedulerControlResponse,
    SchedulerPolicyResponse,
    SchedulerPriorityBucketResponse,
)
from backend.app.audit.service import AuditService
from backend.app.core.typing import string_list
from backend.app.orchestration.blocked_reasons import explain_blocked_reason
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task, TaskStep
from backend.app.workspaces.models import Workspace


class OperationsSchedulerService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def pause_scheduler(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        reason: str | None,
    ) -> SchedulerControlResponse | None:
        workspace = self._session.get(Workspace, workspace_id)
        if workspace is None:
            return None
        settings = dict(workspace.settings or {})
        scheduler = _scheduler_settings(settings)
        scheduler["paused"] = True
        scheduler["pause_reason"] = _non_empty_string_or_none(reason) or "operator_paused"
        settings["scheduler"] = scheduler
        workspace.settings = settings
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=actor_user_id,
            action="workspace.scheduler_paused",
            target_type="workspace",
            target_id=workspace.id,
            metadata={"pause_reason": scheduler["pause_reason"]},
        )
        self._session.commit()
        return SchedulerControlResponse(
            workspace_id=workspace.id,
            paused=True,
            pause_reason=str(scheduler["pause_reason"]),
            cleared_blocked_steps=0,
            policy=self.scheduler_policy(workspace.id),
        )

    def resume_scheduler(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
    ) -> SchedulerControlResponse | None:
        workspace = self._session.get(Workspace, workspace_id)
        if workspace is None:
            return None
        settings = dict(workspace.settings or {})
        scheduler = _scheduler_settings(settings)
        previous_reason = _non_empty_string_or_none(scheduler.get("pause_reason"))
        scheduler["paused"] = False
        scheduler.pop("pause_reason", None)
        settings["scheduler"] = scheduler
        workspace.settings = settings
        cleared = self._clear_workspace_pause_blocks(
            workspace.id,
            reason=previous_reason or "workspace_scheduler_paused",
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=actor_user_id,
            action="workspace.scheduler_resumed",
            target_type="workspace",
            target_id=workspace.id,
            metadata={
                "previous_pause_reason": previous_reason,
                "cleared_blocked_steps": cleared,
            },
        )
        self._session.commit()
        return SchedulerControlResponse(
            workspace_id=workspace.id,
            paused=False,
            pause_reason=None,
            cleared_blocked_steps=cleared,
            policy=self.scheduler_policy(workspace.id),
        )

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
        active_runs = int(
            self._session.scalar(
                select(func.count()).select_from(AgentRun).where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.status.in_(["queued", "running", "waiting_approval"]),
                )
            )
            or 0
        )
        waiting_approval_tasks = int(
            self._session.scalar(
                select(func.count()).select_from(Task).where(
                    Task.workspace_id == workspace_id,
                    Task.status == "waiting_approval",
                )
            )
            or 0
        )
        priority_buckets: dict[int, dict[str, int]] = {}
        blocked_reasons: dict[str, int] = {}
        queued_ages: list[int] = []
        now = datetime.now(UTC)
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
                    max(0, int((now - _aware_datetime(step.created_at)).total_seconds()))
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
                waiting_approval_tasks=waiting_approval_tasks,
                blocked_steps=blocked_steps,
                active_runs=active_runs,
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
            policy=self.scheduler_policy(workspace_id),
        )

    def list_blocked_steps(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        code: str | None = None,
    ) -> tuple[list[BlockedStepExplanationResponse], int]:
        rows = self._session.execute(
            select(TaskStep, Task)
            .join(Task, Task.id == TaskStep.task_id)
            .where(
                TaskStep.workspace_id == workspace_id,
                Task.workspace_id == workspace_id,
                TaskStep.status == "queued",
            )
            .order_by(TaskStep.created_at.asc(), TaskStep.id.asc())
        ).all()
        blocked: list[BlockedStepExplanationResponse] = []
        for step, task in rows:
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
                    priority_score=_positive_int_or_none(dependencies.get("priority_score")),
                    created_at=step.created_at,
                    updated_at=step.updated_at,
                )
            )
        return blocked[page.offset : page.offset + page.limit], len(blocked)

    def unblock_steps(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        code: str | None,
        reason: str | None,
        runtime_space_id: UUID | None,
        limit: int,
    ) -> BlockedStepUnblockResponse:
        normalized_code = _non_empty_string_or_none(code)
        normalized_reason = _non_empty_string_or_none(reason)
        if normalized_code is None and normalized_reason is None and runtime_space_id is None:
            raise ValueError("At least one unblock filter is required")
        rows = self._session.execute(
            select(TaskStep, Task)
            .join(Task, Task.id == TaskStep.task_id)
            .where(
                TaskStep.workspace_id == workspace_id,
                Task.workspace_id == workspace_id,
                TaskStep.status == "queued",
            )
            .order_by(TaskStep.created_at.asc(), TaskStep.id.asc())
        ).all()
        unblocked = 0
        for step, task in rows:
            if unblocked >= limit:
                break
            dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
            if dependencies.get("scheduling_status") != "blocked":
                continue
            explanation = explain_blocked_reason(dependencies.get("blocked_reason"))
            effective_runtime_space_id = step.runtime_space_id or task.runtime_space_id
            if normalized_code is not None and explanation.code != normalized_code:
                continue
            if normalized_reason is not None and explanation.reason != normalized_reason:
                continue
            if runtime_space_id is not None and effective_runtime_space_id != runtime_space_id:
                continue
            updated = dict(dependencies)
            updated.pop("scheduling_status", None)
            updated.pop("blocked_reason", None)
            updated.pop("blocked_resource_keys", None)
            updated.pop("priority_score", None)
            step.dependencies = updated
            unblocked += 1
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="scheduler.blocked_steps_unblocked",
            target_type="workspace",
            target_id=workspace_id,
            metadata={
                "code": normalized_code,
                "reason": normalized_reason,
                "runtime_space_id": str(runtime_space_id) if runtime_space_id else None,
                "limit": limit,
                "unblocked_steps": unblocked,
            },
        )
        self._session.commit()
        return BlockedStepUnblockResponse(
            workspace_id=workspace_id,
            unblocked_steps=unblocked,
        )

    def scheduler_policy(self, workspace_id: UUID) -> SchedulerPolicyResponse:
        workspace = self._session.get(Workspace, workspace_id)
        settings = workspace.settings if workspace is not None else {}
        raw_scheduler = settings.get("scheduler") if isinstance(settings, dict) else None
        scheduler = raw_scheduler if isinstance(raw_scheduler, dict) else {}
        return SchedulerPolicyResponse(
            paused=scheduler.get("paused") is True,
            pause_reason=_non_empty_string_or_none(scheduler.get("pause_reason")),
            max_active_runs=_positive_int_or_none(scheduler.get("max_active_runs")),
            max_running_tasks=_positive_int_or_none(scheduler.get("max_running_tasks")),
            max_runs_to_start_per_tick=_positive_int_or_none(
                scheduler.get("max_runs_to_start_per_tick")
            ),
            max_steps_per_task_per_tick=_positive_int_or_none(
                scheduler.get("max_steps_per_task_per_tick")
            ),
            starvation_boost_after_seconds=_positive_int_or_none(
                scheduler.get("starvation_boost_after_seconds")
            ),
            resource_limits=_positive_number_dict(scheduler.get("resource_limits")),
        )

    def _clear_workspace_pause_blocks(self, workspace_id: UUID, *, reason: str) -> int:
        steps = self._session.scalars(
            select(TaskStep).where(
                TaskStep.workspace_id == workspace_id,
                TaskStep.status == "queued",
            )
        ).all()
        cleared = 0
        for step in steps:
            dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
            if dependencies.get("scheduling_status") != "blocked":
                continue
            if dependencies.get("blocked_reason") != reason:
                continue
            updated = dict(dependencies)
            updated.pop("scheduling_status", None)
            updated.pop("blocked_reason", None)
            updated.pop("priority_score", None)
            step.dependencies = updated
            cleared += 1
        return cleared


def _scheduler_settings(settings: dict[str, object]) -> dict[str, object]:
    raw_scheduler = settings.get("scheduler")
    if not isinstance(raw_scheduler, dict):
        return {}
    return dict(raw_scheduler)


def _non_empty_string_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _positive_int_or_none(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    return None


def _positive_number_dict(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, float] = {}
    for key, item in value.items():
        if isinstance(item, int | float) and not isinstance(item, bool) and item > 0:
            normalized[str(key)] = float(item)
    return normalized


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
