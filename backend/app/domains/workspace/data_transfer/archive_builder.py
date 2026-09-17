from __future__ import annotations

import json
from hashlib import sha256
from io import BytesIO
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domains.workspace.data_transfer.builder import WorkspaceExportBuilder
from backend.app.domains.workspace.data_transfer.contracts import (
    WorkspaceArchiveExportRequest,
    WorkspaceArchiveExportResult,
)
from backend.app.domains.workspace.storage.artifact_models import Artifact
from backend.app.domains.workspace.storage.models import WorkspaceFile
from backend.app.domains.workspace.storage.security import safe_filename
from backend.app.domains.workspace.storage.storage import ObjectStorage
from backend.app.domains.workspace.tenants.models import Workspace
from backend.app.observability.audit.service import AuditService


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
        object_inventory: list[dict[str, object]] = []
        total_bytes = 0
        buffer = BytesIO()
        with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as archive:
            archive.writestr(
                "metadata.json",
                json.dumps(metadata.model_dump(mode="json"), ensure_ascii=False, indent=2),
            )
            if request.include_file_bytes:
                total_bytes = self._write_rows(
                    archive=archive,
                    storage=storage,
                    rows=self._file_rows(workspace.id, request.max_items_per_collection),
                    prefix="files",
                    request=request,
                    current_total=total_bytes,
                    skipped=skipped,
                    object_inventory=object_inventory,
                )
            if request.include_artifact_bytes:
                total_bytes = self._write_rows(
                    archive=archive,
                    storage=storage,
                    rows=self._artifact_rows(workspace.id, request.max_items_per_collection),
                    prefix="artifacts",
                    request=request,
                    current_total=total_bytes,
                    skipped=skipped,
                    object_inventory=object_inventory,
                )
            if skipped:
                archive.writestr("skipped-objects.json", json.dumps(skipped, indent=2))
            archive.writestr(
                "object-inventory.json",
                json.dumps(object_inventory, ensure_ascii=False, indent=2, sort_keys=True),
            )
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
            manifest_checksum_sha256=metadata.manifest.payload_checksum_sha256,
            object_inventory=object_inventory,
        )

    def _write_rows(
        self,
        *,
        archive: ZipFile,
        storage: ObjectStorage,
        rows: list[WorkspaceFile] | list[Artifact],
        prefix: str,
        request: WorkspaceArchiveExportRequest,
        current_total: int,
        skipped: list[str],
        object_inventory: list[dict[str, object]],
    ) -> int:
        total = current_total
        for row in rows:
            total = self._write_blob(
                archive=archive,
                storage=storage,
                storage_key=row.storage_key,
                expected_checksum=row.checksum_sha256,
                archive_name=f"{prefix}/{row.id}/{safe_filename(row.filename)}",
                size_bytes=row.size_bytes,
                max_bytes_per_object=request.max_bytes_per_object,
                max_total_bytes=request.max_total_bytes,
                current_total=total,
                skipped=skipped,
                object_inventory=object_inventory,
            )
        return total

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
        expected_checksum: str,
        archive_name: str,
        size_bytes: int,
        max_bytes_per_object: int,
        max_total_bytes: int,
        current_total: int,
        skipped: list[str],
        object_inventory: list[dict[str, object]],
    ) -> int:
        if size_bytes > max_bytes_per_object:
            skipped.append(f"{archive_name}: object exceeds max_bytes_per_object")
            object_inventory.append(
                {"archive_name": archive_name, "status": "skipped", "reason": "object_too_large"}
            )
            return current_total
        if current_total + size_bytes > max_total_bytes:
            skipped.append(f"{archive_name}: archive exceeds max_total_bytes")
            object_inventory.append(
                {"archive_name": archive_name, "status": "skipped", "reason": "archive_too_large"}
            )
            return current_total
        try:
            content = storage.read(storage_key)
        except FileNotFoundError:
            skipped.append(f"{archive_name}: storage object missing")
            object_inventory.append(
                {"archive_name": archive_name, "status": "skipped", "reason": "object_missing"}
            )
            return current_total
        actual_checksum = sha256(content).hexdigest()
        if actual_checksum != expected_checksum:
            skipped.append(f"{archive_name}: storage checksum mismatch")
            object_inventory.append(
                {
                    "archive_name": archive_name,
                    "status": "skipped",
                    "reason": "storage_checksum_mismatch",
                }
            )
            return current_total
        archive.writestr(archive_name, content)
        object_inventory.append(
            {
                "archive_name": archive_name,
                "resource_id": archive_name.split("/", 2)[1],
                "status": "included",
                "size_bytes": len(content),
                "checksum_sha256": actual_checksum,
            }
        )
        return current_total + len(content)
