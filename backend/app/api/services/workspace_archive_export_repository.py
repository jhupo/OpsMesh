from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.projects.export_models import WorkspaceExportJob
from backend.app.projects.export_status import WorkspaceExportJobStatus
from backend.app.files.storage import ObjectStorage


class WorkspaceArchiveExportJobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, workspace_id: UUID, job_id: UUID) -> WorkspaceExportJob | None:
        return self._session.scalar(
            select(WorkspaceExportJob).where(
                WorkspaceExportJob.workspace_id == workspace_id,
                WorkspaceExportJob.id == job_id,
            )
        )

    def read_completed_content(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        storage: ObjectStorage,
    ) -> tuple[WorkspaceExportJob, bytes]:
        export_job = self.get(workspace_id=workspace_id, job_id=job_id)
        if export_job is None:
            raise FileNotFoundError("Export job not found")
        if export_job.status != WorkspaceExportJobStatus.COMPLETED.value:
            raise ValueError("Export job is not completed")
        if export_job.storage_key is None:
            raise FileNotFoundError("Export artifact is missing")
        return export_job, storage.read(export_job.storage_key)
