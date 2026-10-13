from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.resources.storage.backends import ObjectStorage
from opsmesh.resources.transfers.archive_jobs import WorkspaceArchiveExportJobService
from opsmesh.resources.transfers.builder import WorkspaceExportBuilder
from opsmesh.resources.transfers.contracts import (
    WorkspaceArchiveExportRequest,
    WorkspaceExportRequest,
    WorkspaceExportResponse,
)
from opsmesh.resources.transfers.models import WorkspaceExportJob
from opsmesh.runtime.temporal.contracts import ExecutionRequest
from opsmesh.workspaces.management.models import Workspace


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

    def create_archive_export_job(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        request: WorkspaceArchiveExportRequest,
    ) -> WorkspaceExportJob:
        return self._jobs().create_archive_export_job(
            workspace=workspace,
            user_id=user_id,
            request=request,
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

    def settle_interrupted_export(
        self,
        *,
        request: ExecutionRequest,
    ) -> WorkspaceExportJob | None:
        return self._jobs().settle_interrupted_export(request=request)

    def run_archive_export_job(
        self,
        *,
        request: ExecutionRequest,
        storage: ObjectStorage,
    ) -> WorkspaceExportJob:
        return self._jobs().run_archive_export_job(request=request, storage=storage)

    def _jobs(self) -> WorkspaceArchiveExportJobService:
        return WorkspaceArchiveExportJobService(self._session)
