import json
from hashlib import sha256
from io import BytesIO
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from sqlalchemy.orm import Session

from backend.app.domains.workspace.data_transfer.contracts import (
    SUPPORTED_WORKSPACE_EXPORT_FORMAT,
    WorkspaceArchiveImportRequest,
    WorkspaceExportResponse,
    WorkspaceImportConflict,
    WorkspaceImportRequest,
    WorkspaceImportResponse,
)
from backend.app.domains.workspace.data_transfer.importers.artifacts import (
    WorkspaceArchiveArtifactImporter,
)
from backend.app.domains.workspace.data_transfer.importers.context import _int_field, _string_field
from backend.app.domains.workspace.data_transfer.importers.files import (
    WorkspaceArchiveFileImporter,
)
from backend.app.domains.workspace.data_transfer.importers.metadata import (
    WorkspaceMetadataImportService,
)
from backend.app.domains.workspace.data_transfer.importers.preview import _populate_import_preview
from backend.app.domains.workspace.data_transfer.repository import (
    WorkspaceArchiveBlobReader,
)
from backend.app.domains.workspace.projects.models import WorkspaceProjectFile
from backend.app.domains.workspace.storage.storage import ObjectStorage
from backend.app.domains.workspace.storage.storage_transactions import (
    CompensatingObjectStorageWrites,
    ObjectStorageCompensationError,
)
from backend.app.domains.workspace.tenants.models import Workspace
from backend.app.observability.audit.service import AuditService


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
                self._import_project_files(
                    workspace=workspace,
                    request=request,
                    metadata=metadata,
                    response=response,
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

    def _import_project_files(
        self,
        *,
        workspace: Workspace,
        request: WorkspaceArchiveImportRequest,
        metadata: WorkspaceExportResponse,
        response: WorkspaceImportResponse,
    ) -> None:
        response.created_counts.setdefault("project_files", 0)
        response.skipped_counts.setdefault("project_files", 0)
        response.id_map.setdefault("project_files", {})
        items = metadata.project_files[: request.max_items_per_collection]
        source_ids = {
            _string_field(item, "id")
            for item in items
            if _string_field(item, "id")
            and response.id_map["projects"].get(_string_field(item, "project_id"))
            and response.id_map["files"].get(_string_field(item, "workspace_file_id"))
        }
        pending_supersedes: list[tuple[WorkspaceProjectFile, str]] = []
        for item in items:
            source_id = _string_field(item, "id")
            project_id = response.id_map["projects"].get(_string_field(item, "project_id"))
            file_id = response.id_map["files"].get(_string_field(item, "workspace_file_id"))
            if project_id is None or file_id is None:
                response.skipped_counts["project_files"] += 1
                response.warnings.append(
                    f"Skipped project file {source_id}: project or workspace file was not imported"
                )
                continue
            source_supersedes_id = _string_field(item, "supersedes_project_file_id")
            if source_supersedes_id and source_supersedes_id not in source_ids:
                response.skipped_counts["project_files"] += 1
                response.conflict_plan.append(
                    WorkspaceImportConflict(
                        collection="project_files",
                        source_id=source_id,
                        field="supersedes_project_file_id",
                        source_value=source_supersedes_id,
                        strategy="skip_missing_dependency",
                        severity="warning",
                        message=(
                            f"Skipped project file {source_id}: superseded project file "
                            f"{source_supersedes_id!r} was not imported."
                        ),
                    )
                )
                continue
            response.created_counts["project_files"] += 1
            if request.dry_run:
                continue
            binding = WorkspaceProjectFile(
                workspace_id=workspace.id,
                project_id=UUID(project_id),
                workspace_file_id=UUID(file_id),
                supersedes_project_file_id=None,
                project_path=_string_field(item, "project_path"),
                version=max(_int_field(item, "version", 1), 1),
                access_mode=_string_field(item, "access_mode", "read_only"),
                status=_string_field(item, "status", "active"),
            )
            self._session.add(binding)
            self._session.flush()
            response.id_map["project_files"][source_id] = str(binding.id)
            if source_supersedes_id:
                pending_supersedes.append((binding, source_supersedes_id))
        for binding, source_supersedes_id in pending_supersedes:
            imported_supersedes_id = response.id_map["project_files"].get(source_supersedes_id)
            if imported_supersedes_id is None:
                raise ValueError(
                    f"Project file {binding.id} has an unresolved superseded project file"
                )
            binding.supersedes_project_file_id = UUID(imported_supersedes_id)
        if pending_supersedes:
            self._session.flush()

    def _read_metadata(self, archive: ZipFile) -> WorkspaceExportResponse:
        if "metadata.json" not in archive.namelist():
            raise ValueError("Archive is missing metadata.json")
        metadata = WorkspaceExportResponse.model_validate_json(archive.read("metadata.json"))
        if metadata.manifest.format_version != SUPPORTED_WORKSPACE_EXPORT_FORMAT:
            raise ValueError(
                f"Unsupported workspace archive format: {metadata.manifest.format_version!r}"
            )
        payload = metadata.model_dump(mode="json")
        manifest = payload.pop("manifest")
        expected_checksum = manifest.get("payload_checksum_sha256")
        actual_checksum = sha256(
            json.dumps(
                {
                    "workspace": payload["workspace"],
                    "collections": {
                        key: value for key, value in payload.items() if key != "workspace"
                    },
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if expected_checksum != actual_checksum:
            raise ValueError("Workspace archive metadata checksum mismatch")
        if "object-inventory.json" not in archive.namelist():
            raise ValueError("Workspace archive is missing object inventory")
        inventory = json.loads(archive.read("object-inventory.json"))
        if not isinstance(inventory, list):
            raise ValueError("Workspace archive object inventory is invalid")
        archive_names = set(archive.namelist())
        for item in inventory:
            if not isinstance(item, dict) or item.get("status") != "included":
                continue
            name = item.get("archive_name")
            expected_size = item.get("size_bytes")
            expected_object_checksum = item.get("checksum_sha256")
            if (
                not isinstance(name, str)
                or name not in archive_names
                or not isinstance(expected_size, int)
                or not isinstance(expected_object_checksum, str)
            ):
                raise ValueError("Workspace archive object inventory is invalid")
        return metadata

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
                import_memory=request.import_memory,
                import_projects=request.import_projects,
                name_prefix=request.name_prefix,
                max_items_per_collection=request.max_items_per_collection,
                resolutions=request.resolutions,
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
            artifact_importer.finalize(response)

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
