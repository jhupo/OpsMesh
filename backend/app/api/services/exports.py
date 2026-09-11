from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.api.schemas.exports import (
    WorkspaceArchiveExportRequest,
    WorkspaceArchiveExportResult,
    WorkspaceArchiveRestoreDrillRequest,
    WorkspaceExportRequest,
    WorkspaceExportResponse,
)
from backend.app.api.services.workspace_archive_export_builder import WorkspaceArchiveExportBuilder
from backend.app.api.services.workspace_archive_export_jobs import WorkspaceArchiveExportJobService
from backend.app.api.services.workspace_export_builder import WorkspaceExportBuilder
from backend.app.files.storage import ObjectStorage
from backend.app.projects.export_models import WorkspaceExportJob
from backend.app.workers.jobs import JobPayload
from backend.app.workers.queue.redis_queue import RedisQueue
from backend.app.workspaces.models import Workspace


class WorkspaceExportService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def build_export(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        request: WorkspaceExportRequest,
    ) -> WorkspaceExportResponse:
        return WorkspaceExportBuilder(self._session).build_export(
            workspace=workspace,
            user_id=user_id,
            request=request,
        )

    def build_archive_export(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        request: WorkspaceArchiveExportRequest,
        storage: ObjectStorage,
    ) -> WorkspaceArchiveExportResult:
        return WorkspaceArchiveExportBuilder(self._session).build_archive_export(
            workspace=workspace,
            user_id=user_id,
            request=request,
            storage=storage,
        )

    def create_archive_export_job(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        request: WorkspaceArchiveExportRequest,
        queue: RedisQueue,
    ) -> WorkspaceExportJob:
        return self._jobs().create_archive_export_job(
            workspace=workspace,
            user_id=user_id,
            request=request,
            queue=queue,
        )

    def get_export_job(self, *, workspace_id: UUID, job_id: UUID) -> WorkspaceExportJob | None:
        return self._jobs().get_export_job(workspace_id=workspace_id, job_id=job_id)

    def read_export_job_content(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        storage: ObjectStorage,
    ) -> tuple[WorkspaceExportJob, bytes]:
        return self._jobs().read_export_job_content(
            workspace_id=workspace_id,
            job_id=job_id,
            storage=storage,
        )

    def fail_archive_export_job(
        self,
        *,
        job: JobPayload,
        error: str,
    ) -> WorkspaceExportJob | None:
        return self._jobs().fail_archive_export_job(job=job, error=error)

    def verify_archive_export_job(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        user_id: UUID,
        storage: ObjectStorage,
    ) -> dict[str, object]:
        return self._jobs().verify_archive_export_job(
            workspace_id=workspace_id,
            job_id=job_id,
            user_id=user_id,
            storage=storage,
        )

    def run_archive_restore_drill(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        job_id: UUID,
        request: WorkspaceArchiveRestoreDrillRequest,
        storage: ObjectStorage,
    ) -> dict[str, object]:
        return self._jobs().run_archive_restore_drill(
            workspace=workspace,
            user_id=user_id,
            job_id=job_id,
            request=request,
            storage=storage,
        )

    def run_archive_export_job(
        self,
        *,
        job: JobPayload,
        storage: ObjectStorage,
    ) -> WorkspaceExportJob:
        return self._jobs().run_archive_export_job(job=job, storage=storage)

    def _jobs(self) -> WorkspaceArchiveExportJobService:
        return WorkspaceArchiveExportJobService(self._session)
