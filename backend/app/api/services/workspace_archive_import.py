from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from sqlalchemy.orm import Session

from backend.app.api.schemas.exports import (
    WorkspaceArchiveImportRequest,
    WorkspaceExportResponse,
    WorkspaceImportConflict,
    WorkspaceImportRequest,
    WorkspaceImportResponse,
)
from backend.app.api.services.workspace_import_checksum import _validated_checksum
from backend.app.api.services.workspace_import_fields import (
    _dict_field,
    _int_field,
    _optional_string_field,
    _string_field,
    _uuid_or_none,
)
from backend.app.api.services.workspace_import_preview import _populate_import_preview
from backend.app.api.services.workspace_import_resolution import _archive_resolution_action
from backend.app.api.services.workspace_metadata_import import WorkspaceMetadataImportService
from backend.app.artifacts.models import Artifact
from backend.app.audit.service import AuditService
from backend.app.files.models import WorkspaceFile
from backend.app.files.security import safe_filename
from backend.app.files.storage import ObjectStorage
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
        with archive:
            if "metadata.json" not in archive.namelist():
                raise ValueError("Archive is missing metadata.json")
            metadata = WorkspaceExportResponse.model_validate_json(archive.read("metadata.json"))
            archive_names = set(archive.namelist())
            metadata_importer = WorkspaceMetadataImportService(self._session)
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
            )
            response.created_counts.setdefault("files", 0)
            response.skipped_counts.setdefault("files", 0)
            response.id_map.setdefault("files", {})
            response.created_counts.setdefault("artifacts", 0)
            response.skipped_counts.setdefault("artifacts", 0)
            response.id_map.setdefault("artifacts", {})
            total_bytes = 0
            if request.import_file_bytes:
                for item in metadata.files[: request.max_items_per_collection]:
                    total_bytes = self._import_workspace_file_blob(
                        workspace=workspace,
                        user_id=user_id,
                        request=request,
                        archive=archive,
                        archive_names=archive_names,
                        item=item,
                        response=response,
                        storage=storage,
                        total_bytes=total_bytes,
                    )
            if request.import_artifact_bytes:
                for item in metadata.artifacts[: request.max_items_per_collection]:
                    total_bytes = self._import_artifact_blob(
                        workspace=workspace,
                        request=request,
                        archive=archive,
                        archive_names=archive_names,
                        item=item,
                        response=response,
                        storage=storage,
                        total_bytes=total_bytes,
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
                return response
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
            self._session.commit()
            return response

    def _import_workspace_file_blob(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        request: WorkspaceArchiveImportRequest,
        archive: ZipFile,
        archive_names: set[str],
        item: dict[str, object],
        response: WorkspaceImportResponse,
        storage: ObjectStorage,
        total_bytes: int,
    ) -> int:
        source_id = _string_field(item, "id")
        if _archive_resolution_action(request, "files", source_id) == "exclude_object":
            response.skipped_counts["files"] += 1
            return total_bytes
        filename = safe_filename(_string_field(item, "filename", "file.bin"))
        archive_name = f"files/{source_id}/{filename}"
        content = self._read_import_blob(
            archive=archive,
            archive_names=archive_names,
            archive_name=archive_name,
            source_id=source_id,
            collection="files",
            response=response,
            request=request,
            total_bytes=total_bytes,
        )
        if content is None:
            return total_bytes
        checksum_result = _validated_checksum(
            content=content,
            source_checksum=_string_field(item, "checksum_sha256"),
            source_id=source_id,
            collection="files",
            response=response,
            allow_replace=_archive_resolution_action(
                request,
                "files",
                source_id,
            )
            == "replace_archive_object",
        )
        if not checksum_result.matched:
            response.skipped_counts["files"] += 1
            return total_bytes
        response.created_counts["files"] += 1
        total_bytes += len(content)
        if request.dry_run:
            return total_bytes
        imported_filename = safe_filename(f"{request.name_prefix}{filename}")
        file = WorkspaceFile(
            workspace_id=workspace.id,
            uploaded_by_user_id=user_id,
            filename=imported_filename,
            content_type=_string_field(item, "content_type", "application/octet-stream"),
            size_bytes=len(content),
            checksum_sha256=checksum_result.checksum_sha256,
            storage_key=(
                f"workspaces/{workspace.id}/files/imported/{source_id}/{imported_filename}"
            ),
            status="active",
            file_metadata={
                **_dict_field(item, "metadata"),
                "imported_from_file_id": source_id,
                "import_checksum_matched": checksum_result.matched,
            },
        )
        self._session.add(file)
        self._session.flush()
        storage.write(file.storage_key, content)
        response.id_map["files"][source_id] = str(file.id)
        return total_bytes

    def _import_artifact_blob(
        self,
        *,
        workspace: Workspace,
        request: WorkspaceArchiveImportRequest,
        archive: ZipFile,
        archive_names: set[str],
        item: dict[str, object],
        response: WorkspaceImportResponse,
        storage: ObjectStorage,
        total_bytes: int,
    ) -> int:
        source_id = _string_field(item, "id")
        if _archive_resolution_action(request, "artifacts", source_id) == "exclude_object":
            response.skipped_counts["artifacts"] += 1
            return total_bytes
        filename = safe_filename(_string_field(item, "filename", "artifact.bin"))
        archive_name = f"artifacts/{source_id}/{filename}"
        content = self._read_import_blob(
            archive=archive,
            archive_names=archive_names,
            archive_name=archive_name,
            source_id=source_id,
            collection="artifacts",
            response=response,
            request=request,
            total_bytes=total_bytes,
        )
        if content is None:
            return total_bytes
        checksum_result = _validated_checksum(
            content=content,
            source_checksum=_string_field(item, "checksum_sha256"),
            source_id=source_id,
            collection="artifacts",
            response=response,
            allow_replace=_archive_resolution_action(
                request,
                "artifacts",
                source_id,
            )
            == "replace_archive_object",
        )
        if not checksum_result.matched:
            response.skipped_counts["artifacts"] += 1
            return total_bytes
        response.created_counts["artifacts"] += 1
        total_bytes += len(content)
        if request.dry_run:
            return total_bytes
        source_task_id = _string_field(item, "task_id")
        source_run_id = _string_field(item, "agent_run_id")
        source_step_id = _string_field(item, "task_step_id")
        source_agent_id = _string_field(item, "agent_profile_id")
        source_supersedes_id = _string_field(item, "supersedes_artifact_id")
        imported_task_id = response.id_map["tasks"].get(source_task_id)
        imported_run_id = response.id_map.get("runs", {}).get(source_run_id)
        imported_step_id = response.id_map.get("task_steps", {}).get(source_step_id)
        imported_agent_id = response.id_map.get("agents", {}).get(source_agent_id)
        imported_supersedes_id = response.id_map["artifacts"].get(source_supersedes_id)
        if source_task_id and imported_task_id is None:
            response.warnings.append(
                f"Imported artifact {source_id} without a mapped task"
            )
        if source_run_id and imported_run_id is None:
            response.warnings.append(
                f"Imported artifact {source_id} without a mapped run"
            )
        imported_filename = safe_filename(f"{request.name_prefix}{filename}")
        artifact = Artifact(
            workspace_id=workspace.id,
            task_id=_uuid_or_none(imported_task_id),
            agent_run_id=None,
            task_step_id=_uuid_or_none(imported_step_id),
            agent_profile_id=_uuid_or_none(imported_agent_id),
            supersedes_artifact_id=_uuid_or_none(imported_supersedes_id),
            work_package_id=_optional_string_field(item, "work_package_id"),
            version=_int_field(item, "version", 1),
            review_status=_string_field(item, "review_status", "pending"),
            artifact_type=_string_field(item, "artifact_type", "file"),
            filename=imported_filename,
            content_type=_string_field(item, "content_type", "application/octet-stream"),
            size_bytes=len(content),
            checksum_sha256=checksum_result.checksum_sha256,
            storage_key=(
                f"workspaces/{workspace.id}/artifacts/imported/{source_id}/"
                f"{imported_filename}"
            ),
            artifact_metadata={
                **_dict_field(item, "metadata"),
                "imported_from_artifact_id": source_id,
                "source_task_id": source_task_id or None,
                "source_agent_run_id": source_run_id or None,
                "source_task_step_id": source_step_id or None,
                "source_agent_profile_id": source_agent_id or None,
                "source_supersedes_artifact_id": source_supersedes_id or None,
                "imported_task_id": imported_task_id,
                "imported_agent_run_id": imported_run_id,
                "imported_task_step_id": imported_step_id,
                "imported_agent_profile_id": imported_agent_id,
                "imported_supersedes_artifact_id": imported_supersedes_id,
                "import_checksum_matched": checksum_result.matched,
            },
            created_at=datetime.now(UTC),
        )
        self._session.add(artifact)
        self._session.flush()
        storage.write(artifact.storage_key, content)
        response.id_map["artifacts"][source_id] = str(artifact.id)
        return total_bytes

    def _read_import_blob(
        self,
        *,
        archive: ZipFile,
        archive_names: set[str],
        archive_name: str,
        source_id: str,
        collection: str,
        response: WorkspaceImportResponse,
        request: WorkspaceArchiveImportRequest,
        total_bytes: int,
    ) -> bytes | None:
        if archive_name not in archive_names:
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
        content = archive.read(archive_name)
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

