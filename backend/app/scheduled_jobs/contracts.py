from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.scheduled_jobs.models import WorkspaceScheduledJob
from backend.app.scheduled_jobs.types import ScheduledJobCreate
from backend.app.workers.queue.redis_queue import RedisQueue


class ScheduledJobStore(Protocol):
    _session: Session

    def _require_job(self, workspace_id: UUID, scheduled_job_id: UUID) -> WorkspaceScheduledJob: ...
    def _due_jobs(self, *, limit: int, now: datetime) -> list[WorkspaceScheduledJob]: ...
    def _validate_create_data(self, workspace_id: UUID, data: ScheduledJobCreate) -> None: ...
    def _record_created_audit(
        self, scheduled_job: WorkspaceScheduledJob, *, workspace_id: UUID, user_id: UUID,
    ) -> None: ...
    def _record_paused_audit(
        self, scheduled_job: WorkspaceScheduledJob, *, workspace_id: UUID, user_id: UUID,
    ) -> None: ...
    def _record_resumed_audit(
        self, scheduled_job: WorkspaceScheduledJob, *, workspace_id: UUID, user_id: UUID,
    ) -> None: ...
    def _record_due_audit(
        self, scheduled_job: WorkspaceScheduledJob, *, status: str,
        queued_job_id: UUID | None, message: str | None,
    ) -> None: ...
    def _advance_schedule(
        self, scheduled_job: WorkspaceScheduledJob, *, due_at: datetime, now: datetime,
    ) -> None: ...
    def _apply_due_action(
        self, scheduled_job: WorkspaceScheduledJob, *, due_at: datetime, queue: RedisQueue | None,
    ) -> tuple[str, UUID | None, str | None]: ...
