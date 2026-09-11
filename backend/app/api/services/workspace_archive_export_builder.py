from __future__ import annotations

import json
from io import BytesIO
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.exports import (
    WorkspaceArchiveExportRequest,
    WorkspaceArchiveExportResult,
)
from backend.app.api.services.workspace_export_builder import WorkspaceExportBuilder
from backend.app.files.artifact_models import Artifact
from backend.app.observability.audit_service import AuditService
from backend.app.files.models import WorkspaceFile
from backend.app.files.security import safe_filename
from backend.app.files.storage import ObjectStorage
from backend.app.workspaces.models import Workspace


class WorkspaceArchiveExportBuilder:
    def __init__(self, session: Session) -> None:
        self._session = session

    def build_archive_export(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        request: WorkspaceArchiveExportRequest,
        storage: ObjectStorage,
    ) -> WorkspaceArchiveExportResult:
        metadata = WorkspaceExportBuilder(self._session).build_export(
            workspace=workspace, user_id=user_id, request=request
        )
        skipped: list[str] = []
        total_bytes = 0
        buffer = BytesIO()
        with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as archive:
            archive.writestr(
                "metadata.json",
                json.dumps(metadata.model_dump(mode="json"), ensure_ascii=False, indent=2),
            )
            if request.include_file_bytes:
                file_rows = self._file_rows(workspace.id, request.max_items_per_collection)
                for file in file_rows:
                    total_bytes = self._write_blob(
                        archive=archive,
                        storage=storage,
                        storage_key=file.storage_key,
                        archive_name=f"files/{file.id}/{safe_filename(file.filename)}",
                        size_bytes=file.size_bytes,
                        max_bytes_per_object=request.max_bytes_per_object,
                        max_total_bytes=request.max_total_bytes,
                        current_total=total_bytes,
                        skipped=skipped,
                    )
            if request.include_artifact_bytes:
                artifact_rows = self._artifact_rows(workspace.id, request.max_items_per_collection)
                for artifact in artifact_rows:
                    total_bytes = self._write_blob(
                        archive=archive,
                        storage=storage,
                        storage_key=artifact.storage_key,
                        archive_name=f"artifacts/{artifact.id}/{safe_filename(artifact.filename)}",
                        size_bytes=artifact.size_bytes,
                        max_bytes_per_object=request.max_bytes_per_object,
                        max_total_bytes=request.max_total_bytes,
                        current_total=total_bytes,
                        skipped=skipped,
                    )
            if skipped:
                archive.writestr("skipped-objects.json", json.dumps(skipped, indent=2))
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=user_id,
            action="workspace.archive_export.created",
            target_type="workspace",
            target_id=workspace.id,
            metadata={
                "included_file_bytes": request.include_file_bytes,
                "included_artifact_bytes": request.include_artifact_bytes,
                "skipped_objects": skipped,
            },
        )
        self._session.commit()
        return WorkspaceArchiveExportResult(
            filename=f"{workspace.slug}-workspace-archive.zip",
            content=buffer.getvalue(),
            skipped_objects=skipped,
            manifest_counts=metadata.manifest.counts,
        )

    def _file_rows(self, workspace_id: UUID, limit: int) -> list[WorkspaceFile]:
        return list(
            self._session.scalars(
                select(WorkspaceFile)
                .where(WorkspaceFile.workspace_id == workspace_id, WorkspaceFile.status == "active")
                .limit(limit)
            )
        )

    def _artifact_rows(self, workspace_id: UUID, limit: int) -> list[Artifact]:
        return list(
            self._session.scalars(
                select(Artifact).where(Artifact.workspace_id == workspace_id).limit(limit)
            )
        )

    def _write_blob(
        self,
        *,
        archive: ZipFile,
        storage: ObjectStorage,
        storage_key: str,
        archive_name: str,
        size_bytes: int,
        max_bytes_per_object: int,
        max_total_bytes: int,
        current_total: int,
        skipped: list[str],
    ) -> int:
        if size_bytes > max_bytes_per_object:
            skipped.append(f"{archive_name}: object exceeds max_bytes_per_object")
            return current_total
        if current_total + size_bytes > max_total_bytes:
            skipped.append(f"{archive_name}: archive exceeds max_total_bytes")
            return current_total
        try:
            content = storage.read(storage_key)
        except FileNotFoundError:
            skipped.append(f"{archive_name}: storage object missing")
            return current_total
        archive.writestr(archive_name, content)
        return current_total + len(content)
