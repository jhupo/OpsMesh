from zipfile import ZipFile

from backend.app.api.schemas.exports import (
    WorkspaceArchiveImportRequest,
    WorkspaceImportConflict,
    WorkspaceImportResponse,
)


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
