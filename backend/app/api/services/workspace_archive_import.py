from io import BytesIO
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from sqlalchemy.orm import Session

from backend.app.api.schemas.exports import (
    WorkspaceArchiveImportRequest,
    WorkspaceExportResponse,
    WorkspaceImportRequest,
    WorkspaceImportResponse,
)
from backend.app.api.services.workspace_archive_artifact_import import (
    WorkspaceArchiveArtifactImporter,
)
from backend.app.api.services.workspace_archive_blob_reader import WorkspaceArchiveBlobReader
from backend.app.api.services.workspace_archive_file_import import WorkspaceArchiveFileImporter
from backend.app.api.services.workspace_import_preview import _populate_import_preview
from backend.app.api.services.workspace_metadata_import import WorkspaceMetadataImportService
from backend.app.files.storage import ObjectStorage
from backend.app.files.storage_transactions import (
    CompensatingObjectStorageWrites,
    ObjectStorageCompensationError,
)
from backend.app.observability.audit_service import AuditService
from backend.app.workspaces.models import Workspace


class WorkspaceArchiveImportService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def import_archive(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        archive_bytes: bytes,
        request: WorkspaceArchiveImportRequest,
        storage: ObjectStorage,
    ) -> WorkspaceImportResponse:
        try:
            archive = ZipFile(BytesIO(archive_bytes))
        except BadZipFile as exc:
            raise ValueError("Archive is not a valid zip file") from exc
        storage_writes = CompensatingObjectStorageWrites(storage)
        try:
            with archive:
                metadata = self._read_metadata(archive)
                metadata_importer = WorkspaceMetadataImportService(self._session)
                response = self._import_metadata(
                    workspace=workspace,
                    user_id=user_id,
                    request=request,
                    metadata=metadata,
                    metadata_importer=metadata_importer,
                )
                self._import_blobs(
                    workspace=workspace,
                    user_id=user_id,
                    archive=archive,
                    metadata=metadata,
                    request=request,
                    response=response,
                    storage_writes=storage_writes,
                )
                _populate_import_preview(response, metadata)
                if request.dry_run:
                    self._session.rollback()
                    metadata_importer.record_import_preview(
                        workspace_id=workspace.id,
                        user_id=user_id,
                        action="workspace.archive_import.previewed",
                        response=response,
                    )
                    storage_writes.complete()
                    return response
                self._record_archive_import(workspace, user_id, metadata, response)
                self._session.commit()
        except Exception:
            self._session.rollback()
            try:
                storage_writes.compensate()
            except ObjectStorageCompensationError as compensation_exc:
                raise RuntimeError(
                    "Workspace archive import failed and storage cleanup was incomplete"
                ) from compensation_exc
            raise
        storage_writes.complete()
        return response

    def _read_metadata(self, archive: ZipFile) -> WorkspaceExportResponse:
        if "metadata.json" not in archive.namelist():
            raise ValueError("Archive is missing metadata.json")
        return WorkspaceExportResponse.model_validate_json(archive.read("metadata.json"))

    def _import_metadata(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        request: WorkspaceArchiveImportRequest,
        metadata: WorkspaceExportResponse,
        metadata_importer: WorkspaceMetadataImportService,
    ) -> WorkspaceImportResponse:
        response = metadata_importer.import_metadata(
            workspace=workspace,
            user_id=user_id,
            request=WorkspaceImportRequest(
                export=metadata,
                dry_run=request.dry_run,
                import_agents=request.import_agents,
                import_teams=request.import_teams,
                import_tasks=request.import_tasks,
                import_runtime_spaces=request.import_runtime_spaces,
                import_skill_installs=request.import_skill_installs,
                name_prefix=request.name_prefix,
                max_items_per_collection=request.max_items_per_collection,
            ),
            record_preview=False,
            commit=False,
        )
        response.created_counts.setdefault("files", 0)
        response.skipped_counts.setdefault("files", 0)
        response.id_map.setdefault("files", {})
        response.created_counts.setdefault("artifacts", 0)
        response.skipped_counts.setdefault("artifacts", 0)
        response.id_map.setdefault("artifacts", {})
        return response

    def _import_blobs(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        archive: ZipFile,
        metadata: WorkspaceExportResponse,
        request: WorkspaceArchiveImportRequest,
        response: WorkspaceImportResponse,
        storage_writes: CompensatingObjectStorageWrites,
    ) -> None:
        blob_reader = WorkspaceArchiveBlobReader(archive, set(archive.namelist()))
        file_importer = WorkspaceArchiveFileImporter(
            session=self._session,
            blob_reader=blob_reader,
            storage_writes=storage_writes,
        )
        artifact_importer = WorkspaceArchiveArtifactImporter(
            session=self._session,
            blob_reader=blob_reader,
            storage_writes=storage_writes,
        )
        total_bytes = 0
        if request.import_file_bytes:
            for item in metadata.files[: request.max_items_per_collection]:
                total_bytes = file_importer.import_blob(
                    workspace=workspace,
                    user_id=user_id,
                    request=request,
                    item=item,
                    response=response,
                    total_bytes=total_bytes,
                )
        if request.import_artifact_bytes:
            for item in metadata.artifacts[: request.max_items_per_collection]:
                total_bytes = artifact_importer.import_blob(
                    workspace=workspace,
                    request=request,
                    item=item,
                    response=response,
                    total_bytes=total_bytes,
                )

    def _record_archive_import(
        self,
        workspace: Workspace,
        user_id: UUID,
        metadata: WorkspaceExportResponse,
        response: WorkspaceImportResponse,
    ) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=user_id,
            action="workspace.archive_import.created",
            target_type="workspace",
            target_id=workspace.id,
            metadata={
                "source_workspace_id": str(metadata.manifest.workspace_id),
                "created_counts": response.created_counts,
                "skipped_counts": response.skipped_counts,
            },
        )
