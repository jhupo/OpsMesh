from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from backend.app.api.schemas.exports import (
    WorkspaceArchiveImportRequest,
    WorkspaceImportResponse,
)
from backend.app.api.services.workspace_archive_blob_reader import WorkspaceArchiveBlobReader
from backend.app.api.services.workspace_import_checksum import _validated_checksum
from backend.app.api.services.workspace_import_fields import _dict_field, _string_field
from backend.app.api.services.workspace_import_resolution import _archive_resolution_action
from backend.app.files.models import WorkspaceFile
from backend.app.files.security import safe_filename
from backend.app.files.storage_transactions import CompensatingObjectStorageWrites
from backend.app.workspaces.models import Workspace


class WorkspaceArchiveFileImporter:
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
        user_id: UUID,
        request: WorkspaceArchiveImportRequest,
        item: dict[str, object],
        response: WorkspaceImportResponse,
        total_bytes: int,
    ) -> int:
        source_id = _string_field(item, "id")
        if _archive_resolution_action(request, "files", source_id) == "exclude_object":
            response.skipped_counts["files"] += 1
            return total_bytes
        filename = safe_filename(_string_field(item, "filename", "file.bin"))
        content = self._blob_reader.read_blob(
            archive_name=f"files/{source_id}/{filename}",
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
        file_id = uuid4()
        file = WorkspaceFile(
            id=file_id,
            workspace_id=workspace.id,
            uploaded_by_user_id=user_id,
            filename=imported_filename,
            content_type=_string_field(item, "content_type", "application/octet-stream"),
            size_bytes=len(content),
            checksum_sha256=checksum_result.checksum_sha256,
            storage_key=f"workspaces/{workspace.id}/files/{file_id}/{imported_filename}",
            status="active",
            file_metadata={
                **_dict_field(item, "metadata"),
                "imported_from_file_id": source_id,
                "import_checksum_matched": checksum_result.matched,
            },
        )
        self._session.add(file)
        self._session.flush()
        self._storage_writes.write_new(file.storage_key, content)
        response.id_map["files"][source_id] = str(file.id)
        return total_bytes
