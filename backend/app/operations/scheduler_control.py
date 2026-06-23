from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.operations import SchedulerControlResponse
from backend.app.audit.service import AuditService
from backend.app.operations.scheduler_policy import (
    SchedulerPolicyService,
    non_empty_string_or_none,
    scheduler_settings,
)
from backend.app.tasks.models import TaskStep
from backend.app.workspaces.models import Workspace


class SchedulerControlService:
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
        return SchedulerControlResponse(
            workspace_id=workspace.id,
            paused=True,
            pause_reason=str(scheduler["pause_reason"]),
            cleared_blocked_steps=0,
            policy=SchedulerPolicyService(self._session).scheduler_policy(workspace.id),
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
        return SchedulerControlResponse(
            workspace_id=workspace.id,
            paused=False,
            pause_reason=None,
            cleared_blocked_steps=cleared,
            policy=SchedulerPolicyService(self._session).scheduler_policy(workspace.id),
        )

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
