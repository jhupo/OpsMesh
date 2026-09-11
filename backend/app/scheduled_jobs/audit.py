from __future__ import annotations

from uuid import UUID

from backend.app.observability.audit_service import AuditService
from backend.app.scheduled_jobs.contracts import ScheduledJobStore
from backend.app.scheduled_jobs.models import WorkspaceScheduledJob
from backend.app.workspaces.models import Workspace


class ScheduledJobAuditMixin(ScheduledJobStore):
    def _record_created_audit(
        self,
        scheduled_job: WorkspaceScheduledJob,
        *,
        workspace_id: UUID,
        user_id: UUID,
    ) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="workspace.scheduled_job.created",
            target_type="workspace_scheduled_job",
            target_id=scheduled_job.id,
            metadata={
                "name": scheduled_job.name,
                "schedule_type": scheduled_job.schedule_type,
                "schedule_config": scheduled_job.schedule_config,
                "action_type": scheduled_job.action_type,
                "job_type": scheduled_job.job_type,
                "metadata": scheduled_job.metadata_,
            },
        )

    def _record_paused_audit(
        self,
        scheduled_job: WorkspaceScheduledJob,
        *,
        workspace_id: UUID,
        user_id: UUID,
    ) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="workspace.scheduled_job.paused",
            target_type="workspace_scheduled_job",
            target_id=scheduled_job.id,
            metadata={"name": scheduled_job.name},
        )

    def _record_resumed_audit(
        self,
        scheduled_job: WorkspaceScheduledJob,
        *,
        workspace_id: UUID,
        user_id: UUID,
    ) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="workspace.scheduled_job.resumed",
            target_type="workspace_scheduled_job",
            target_id=scheduled_job.id,
            metadata={
                "name": scheduled_job.name,
                "next_run_at": scheduled_job.next_run_at.isoformat()
                if scheduled_job.next_run_at is not None
                else None,
            },
        )

    def _record_due_audit(
        self,
        scheduled_job: WorkspaceScheduledJob,
        *,
        status: str,
        queued_job_id: UUID | None,
        message: str | None,
    ) -> None:
        actor_user_id = scheduled_job.created_by_user_id
        if actor_user_id is None:
            workspace = self._session.get(Workspace, scheduled_job.workspace_id)
            actor_user_id = workspace.owner_user_id if workspace is not None else None
        if actor_user_id is None:
            return
        AuditService(self._session).record_user_action(
            workspace_id=scheduled_job.workspace_id,
            user_id=actor_user_id,
            action="workspace.scheduled_job.due",
            target_type="workspace_scheduled_job",
            target_id=scheduled_job.id,
            metadata={
                "status": status,
                "queued_job_id": str(queued_job_id) if queued_job_id is not None else None,
                "message": message,
                "action_type": scheduled_job.action_type,
                "job_type": scheduled_job.job_type,
                "metadata": scheduled_job.metadata_,
            },
        )
