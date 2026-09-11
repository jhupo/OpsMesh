from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from backend.app.api.schemas.exports import (
    WorkspaceArchiveImportRequest,
    WorkspaceImportResponse,
)
from backend.app.api.services.workspace_archive_blob_reader import WorkspaceArchiveBlobReader
from backend.app.api.services.workspace_import_checksum import _validated_checksum
from backend.app.api.services.workspace_import_fields import (
    _dict_field,
    _int_field,
    _optional_string_field,
    _string_field,
    _uuid_or_none,
)
from backend.app.api.services.workspace_import_resolution import _archive_resolution_action
from backend.app.files.artifact_models import Artifact
from backend.app.files.security import safe_filename
from backend.app.files.storage_transactions import CompensatingObjectStorageWrites
from backend.app.workspaces.models import Workspace


class WorkspaceArchiveArtifactImporter:
    def __init__(
        self,
        *,
        session: Session,
        blob_reader: WorkspaceArchiveBlobReader,
        storage_writes: CompensatingObjectStorageWrites,
    ) -> None:
        self._session = session
        self._blob_reader = blob_reader
        self._storage_writes = storage_writes

    def import_blob(
        self,
        *,
        workspace: Workspace,
        request: WorkspaceArchiveImportRequest,
        item: dict[str, object],
        response: WorkspaceImportResponse,
        total_bytes: int,
    ) -> int:
        source_id = _string_field(item, "id")
        if _archive_resolution_action(request, "artifacts", source_id) == "exclude_object":
            response.skipped_counts["artifacts"] += 1
            return total_bytes
        filename = safe_filename(_string_field(item, "filename", "artifact.bin"))
        content = self._blob_reader.read_blob(
            archive_name=f"artifacts/{source_id}/{filename}",
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
        self._create_artifact(
            workspace=workspace,
            request=request,
            item=item,
            response=response,
            source_id=source_id,
            filename=filename,
            content=content,
            checksum_sha256=checksum_result.checksum_sha256,
            checksum_matched=checksum_result.matched,
        )
        return total_bytes

    def _create_artifact(
        self,
        *,
        workspace: Workspace,
        request: WorkspaceArchiveImportRequest,
        item: dict[str, object],
        response: WorkspaceImportResponse,
        source_id: str,
        filename: str,
        content: bytes,
        checksum_sha256: str,
        checksum_matched: bool,
    ) -> None:
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
            response.warnings.append(f"Imported artifact {source_id} without a mapped task")
        if source_run_id and imported_run_id is None:
            response.warnings.append(f"Imported artifact {source_id} without a mapped run")
        imported_filename = safe_filename(f"{request.name_prefix}{filename}")
        artifact_id = uuid4()
        artifact = Artifact(
            id=artifact_id,
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
            checksum_sha256=checksum_sha256,
            storage_key=f"workspaces/{workspace.id}/artifacts/{artifact_id}/{imported_filename}",
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
                "import_checksum_matched": checksum_matched,
            },
            created_at=datetime.now(UTC),
        )
        self._session.add(artifact)
        self._session.flush()
        self._storage_writes.write_new(artifact.storage_key, content)
        response.id_map["artifacts"][source_id] = str(artifact.id)
