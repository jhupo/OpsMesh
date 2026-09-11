from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.operations import (
    BlockedStepExplanationResponse,
    BlockedStepUnblockResponse,
)
from backend.app.audit.service import AuditService
from backend.app.core.pagination import PageParams
from backend.app.core.typing import string_list
from backend.app.operations.scheduler_policy import non_empty_string_or_none
from backend.app.operations.utils import positive_int_or_none
from backend.app.orchestration.policies.blocked_reasons import explain_blocked_reason
from backend.app.tasks.models import Task, TaskStep


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
        normalized_code = non_empty_string_or_none(code)
        normalized_reason = non_empty_string_or_none(reason)
        if normalized_code is None and normalized_reason is None and runtime_space_id is None:
            raise ValueError("At least one unblock filter is required")
        unblocked = 0
        for step, task in self._blocked_step_rows(workspace_id):
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
