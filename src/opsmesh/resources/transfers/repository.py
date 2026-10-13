"""Persistence and archive-byte access for workspace transfers."""

from __future__ import annotations

from hashlib import sha256
from uuid import UUID
from zipfile import ZipFile

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.resources.storage.backends import ObjectStorage
from opsmesh.resources.transfers.archive_limits import (
    MAX_ARCHIVE_TOTAL_OBJECT_BYTES,
    ArchiveReadLimitError,
    BoundedArchiveReader,
    compressed_archive_limit,
)
from opsmesh.resources.transfers.contracts import (
    WorkspaceArchiveImportRequest,
    WorkspaceImportConflict,
    WorkspaceImportResponse,
)
from opsmesh.resources.transfers.models import WorkspaceExportJob, WorkspaceExportJobStatus


class WorkspaceArchiveBlobReader:
    def __init__(self, archive: ZipFile, *, max_total_bytes: int) -> None:
        self._reader = BoundedArchiveReader(archive, max_total_bytes=max_total_bytes)

    def read_blob(
        self,
        *,
        archive_name: str,
        source_id: str,
        collection: str,
        response: WorkspaceImportResponse,
        request: WorkspaceArchiveImportRequest,
        expected_checksum: str,
        allow_checksum_replacement: bool = False,
    ) -> bytes | None:
        if archive_name not in self._reader.names:
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
        try:
            content = self._reader.read(archive_name, max_bytes=request.max_bytes_per_object)
        except ArchiveReadLimitError as exc:
            response.skipped_counts[collection] += 1
            field = "total_bytes" if exc.total else "size_bytes"
            limit_name = "max_total_bytes" if exc.total else "max_bytes_per_object"
            response.warnings.append(f"Skipped {collection[:-1]} {source_id}: {limit_name} reached")
            response.conflict_plan.append(
                WorkspaceImportConflict(
                    collection=collection,
                    source_id=source_id,
                    field=field,
                    source_value=str(exc.size),
                    target_value=str(exc.limit),
                    strategy="reject",
                    severity="error",
                    message=f"{collection[:-1].title()} exceeds {limit_name} "
                    f"({exc.size} > {exc.limit}).",
                )
            )
            return None
        if sha256(content).hexdigest() != expected_checksum and not allow_checksum_replacement:
            response.skipped_counts[collection] += 1
            response.conflict_plan.append(
                WorkspaceImportConflict(
                    collection=collection,
                    source_id=source_id,
                    field="checksum_sha256",
                    strategy="reject",
                    severity="error",
                    message=f"{collection[:-1].title()} bytes failed checksum validation.",
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
        key, checksum = export_job.storage_key, export_job.checksum_sha256
        self._session.commit()
        content = storage.read_limited(
            key, compressed_archive_limit(MAX_ARCHIVE_TOTAL_OBJECT_BYTES)
        )
        self._session.refresh(export_job)
        if (
            export_job.status != WorkspaceExportJobStatus.COMPLETED.value
            or export_job.storage_key != key
            or export_job.checksum_sha256 != checksum
        ):
            raise ValueError("Archive export changed during download")
        return export_job, content
