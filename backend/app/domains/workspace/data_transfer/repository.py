"""Persistence and archive-byte access for workspace transfers."""

from __future__ import annotations

from uuid import UUID
from zipfile import ZipFile

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domains.workspace.data_transfer.contracts import (
    WorkspaceArchiveImportRequest,
    WorkspaceImportConflict,
    WorkspaceImportResponse,
)
from backend.app.domains.workspace.data_transfer.models import (
    WorkspaceExportJob,
    WorkspaceExportJobStatus,
)
from backend.app.domains.workspace.storage.storage import ObjectStorage


class WorkspaceArchiveBlobReader:
    def __init__(self, archive: ZipFile, archive_names: set[str]) -> None:
        self._archive = archive
        self._archive_names = archive_names

    def read_blob(
        self,
        *,
        archive_name: str,
        source_id: str,
        collection: str,
        response: WorkspaceImportResponse,
        request: WorkspaceArchiveImportRequest,
        total_bytes: int,
    ) -> bytes | None:
        if archive_name not in self._archive_names:
            response.skipped_counts[collection] += 1
            response.warnings.append(f"Skipped {collection[:-1]} {source_id}: bytes not found")
            response.conflict_plan.append(
                WorkspaceImportConflict(
                    collection=collection,
                    source_id=source_id,
                    field="bytes",
                    strategy="skip",
                    severity="warning",
                    message=f"{collection[:-1].title()} bytes are missing from the archive.",
                )
            )
            return None
        content = self._archive.read(archive_name)
        if len(content) > request.max_bytes_per_object:
            response.skipped_counts[collection] += 1
            response.warnings.append(f"Skipped {collection[:-1]} {source_id}: object too large")
            response.conflict_plan.append(
                WorkspaceImportConflict(
                    collection=collection,
                    source_id=source_id,
                    field="size_bytes",
                    source_value=str(len(content)),
                    target_value=str(request.max_bytes_per_object),
                    strategy="reject",
                    severity="error",
                    message=(
                        f"{collection[:-1].title()} exceeds max_bytes_per_object "
                        f"({len(content)} > {request.max_bytes_per_object})."
                    ),
                )
            )
            return None
        if total_bytes + len(content) > request.max_total_bytes:
            response.skipped_counts[collection] += 1
            response.warnings.append(
                f"Skipped {collection[:-1]} {source_id}: archive byte limit reached"
            )
            response.conflict_plan.append(
                WorkspaceImportConflict(
                    collection=collection,
                    source_id=source_id,
                    field="total_bytes",
                    source_value=str(total_bytes + len(content)),
                    target_value=str(request.max_total_bytes),
                    strategy="reject",
                    severity="error",
                    message=(
                        f"Archive import would exceed max_total_bytes "
                        f"({total_bytes + len(content)} > {request.max_total_bytes})."
                    ),
                )
            )
            return None
        return content


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
