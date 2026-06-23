from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from backend.app.scheduled_jobs.constants import ACTIVE_STATUS, COMPLETED_STATUS, PAUSED_STATUS
from backend.app.scheduled_jobs.models import WorkspaceScheduledJob
from backend.app.scheduled_jobs.schedule import next_run_at, utc_datetime
from backend.app.scheduled_jobs.types import ScheduledJobCreate
from backend.app.workspaces.models import Workspace


class ScheduledJobLifecycleMixin:
    def create(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        data: ScheduledJobCreate,
        now: datetime | None = None,
    ) -> WorkspaceScheduledJob:
        current_time = utc_datetime(now)
        self._validate_create_data(workspace.id, data)
        scheduled_job = WorkspaceScheduledJob(
            workspace_id=workspace.id,
            created_by_user_id=user_id,
            name=data.name,
            schedule_type=data.schedule_type,
            schedule_config=data.schedule_config,
            status=ACTIVE_STATUS,
            action_type=data.action_type,
            job_type=data.job_type,
            resource_id=data.resource_id,
            routing=data.routing,
            priority=data.priority,
            max_attempts=data.max_attempts,
            metadata_=data.metadata,
            next_run_at=next_run_at(
                schedule_type=data.schedule_type,
                schedule_config=data.schedule_config,
                after=current_time,
                include_now=True,
            ),
        )
        self._session.add(scheduled_job)
        self._session.flush([scheduled_job])
        self._record_created_audit(scheduled_job, workspace_id=workspace.id, user_id=user_id)
        self._session.commit()
        self._session.refresh(scheduled_job)
        return scheduled_job

    def pause(
        self,
        *,
        workspace_id: UUID,
        scheduled_job_id: UUID,
        user_id: UUID,
    ) -> WorkspaceScheduledJob:
        scheduled_job = self._require_job(workspace_id, scheduled_job_id)
        if scheduled_job.status == ACTIVE_STATUS:
            scheduled_job.status = PAUSED_STATUS
            scheduled_job.paused_at = datetime.now(UTC)
            self._record_paused_audit(scheduled_job, workspace_id=workspace_id, user_id=user_id)
            self._session.commit()
            self._session.refresh(scheduled_job)
        return scheduled_job

    def resume(
        self,
        *,
        workspace_id: UUID,
        scheduled_job_id: UUID,
        user_id: UUID,
        now: datetime | None = None,
    ) -> WorkspaceScheduledJob:
        scheduled_job = self._require_job(workspace_id, scheduled_job_id)
        if scheduled_job.status == PAUSED_STATUS:
            current_time = utc_datetime(now)
            scheduled_job.status = ACTIVE_STATUS
            scheduled_job.paused_at = None
            scheduled_job.next_run_at = next_run_at(
                schedule_type=scheduled_job.schedule_type,
                schedule_config=scheduled_job.schedule_config,
                after=current_time,
                include_now=True,
            )
            self._record_resumed_audit(scheduled_job, workspace_id=workspace_id, user_id=user_id)
            self._session.commit()
            self._session.refresh(scheduled_job)
        return scheduled_job

    def _advance_schedule(
        self,
        scheduled_job: WorkspaceScheduledJob,
        *,
        due_at: datetime,
        now: datetime,
    ) -> None:
        scheduled_job.last_run_at = now
        if scheduled_job.schedule_type == "one_shot":
            scheduled_job.status = COMPLETED_STATUS
            scheduled_job.next_run_at = None
            scheduled_job.completed_at = now
            return
        scheduled_job.next_run_at = next_run_at(
            schedule_type=scheduled_job.schedule_type,
            schedule_config=scheduled_job.schedule_config,
            after=max(utc_datetime(due_at), utc_datetime(now)),
            include_now=False,
        )
