from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.utils import non_empty_string_or_none
from backend.app.domains.orchestration.tasks.models import Task, TaskStep
from backend.app.domains.orchestration.workflows.definitions.blocked_reasons import (
    explain_blocked_reason,
)
from backend.app.domains.workspace.tenants.models import Workspace
from backend.app.domains.workspace.tenants.settings import scheduler_settings
from backend.app.observability.audit.service import AuditService


class SchedulerBlockedStepControlService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def unblock_steps(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        code: str | None,
        reason: str | None,
        runtime_space_id: UUID | None,
        limit: int,
    ) -> int:
        normalized_code = non_empty_string_or_none(code)
        normalized_reason = non_empty_string_or_none(reason)
        if normalized_code is None and normalized_reason is None and runtime_space_id is None:
            raise ValueError("At least one unblock filter is required")
        unblocked = 0
        for step, task in self._blocked_steps(workspace_id):
            if unblocked >= limit:
                break
            dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
            if dependencies.get("scheduling_status") != "blocked":
                continue
            explanation = explain_blocked_reason(dependencies.get("blocked_reason"))
            if normalized_code is not None and explanation.code != normalized_code:
                continue
            if normalized_reason is not None and explanation.reason != normalized_reason:
                continue
            if (
                runtime_space_id is not None
                and (step.runtime_space_id or task.runtime_space_id) != runtime_space_id
            ):
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
        return unblocked

    def _blocked_steps(self, workspace_id: UUID) -> list[tuple[TaskStep, Task]]:
        return list(
            self._session.execute(
                select(TaskStep, Task)
                .join(Task, Task.id == TaskStep.task_id)
                .where(
                    TaskStep.workspace_id == workspace_id,
                    Task.workspace_id == workspace_id,
                    TaskStep.status == "queued",
                )
            ).tuples()
        )


class SchedulerControlService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def pause_scheduler(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        reason: str | None,
    ) -> tuple[Workspace, int] | None:
        workspace = self._session.get(Workspace, workspace_id)
        if workspace is None:
            return None
        settings = dict(workspace.settings or {})
        scheduler = scheduler_settings(settings)
        scheduler["paused"] = True
        scheduler["pause_reason"] = non_empty_string_or_none(reason) or "operator_paused"
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
        return workspace, 0

    def resume_scheduler(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
    ) -> tuple[Workspace, int] | None:
        workspace = self._session.get(Workspace, workspace_id)
        if workspace is None:
            return None
        settings = dict(workspace.settings or {})
        scheduler = scheduler_settings(settings)
        previous_reason = non_empty_string_or_none(scheduler.get("pause_reason"))
        scheduler["paused"] = False
        scheduler.pop("pause_reason", None)
        settings["scheduler"] = scheduler
        workspace.settings = settings
        cleared = self.clear_workspace_pause_blocks(
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
        return workspace, cleared

    def clear_workspace_pause_blocks(self, workspace_id: UUID, *, reason: str) -> int:
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
