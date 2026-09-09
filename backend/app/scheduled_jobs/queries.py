from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, select

from backend.app.db.pagination import page_scalars_by_offset
from backend.app.scheduled_jobs.constants import ACTIVE_STATUS
from backend.app.scheduled_jobs.contracts import ScheduledJobStore
from backend.app.scheduled_jobs.models import WorkspaceScheduledJob


class ScheduledJobQueryMixin(ScheduledJobStore):
    def list_jobs(
        self,
        *,
        workspace_id: UUID,
        limit: int,
        offset: int,
        status: str | None = None,
    ) -> tuple[list[WorkspaceScheduledJob], int]:
        statement = self._workspace_statement(workspace_id)
        if status is not None:
            statement = statement.where(WorkspaceScheduledJob.status == status)
        statement = statement.order_by(
            WorkspaceScheduledJob.created_at.desc(),
            WorkspaceScheduledJob.id.desc(),
        )
        return page_scalars_by_offset(self._session, statement, limit=limit, offset=offset)

    def _due_jobs(self, *, limit: int, now: datetime) -> list[WorkspaceScheduledJob]:
        statement = (
            select(WorkspaceScheduledJob)
            .where(
                WorkspaceScheduledJob.status == ACTIVE_STATUS,
                WorkspaceScheduledJob.next_run_at.is_not(None),
                WorkspaceScheduledJob.next_run_at <= now,
            )
            .order_by(WorkspaceScheduledJob.next_run_at.asc(), WorkspaceScheduledJob.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(self._session.scalars(statement).all())

    def _workspace_statement(
        self,
        workspace_id: UUID,
    ) -> Select[tuple[WorkspaceScheduledJob]]:
        return select(WorkspaceScheduledJob).where(
            WorkspaceScheduledJob.workspace_id == workspace_id
        )

    def _require_job(
        self,
        workspace_id: UUID,
        scheduled_job_id: UUID,
    ) -> WorkspaceScheduledJob:
        scheduled_job = self._session.scalar(
            self._workspace_statement(workspace_id).where(
                WorkspaceScheduledJob.id == scheduled_job_id
            )
        )
        if scheduled_job is None:
            raise ValueError("Scheduled job not found")
        return scheduled_job
