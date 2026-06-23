from dataclasses import dataclass
from hashlib import sha256

from backend.app.api.schemas.exports import WorkspaceImportResponse
from backend.app.api.services.workspace_import_conflicts import _checksum_conflict


@dataclass(frozen=True)
class _ChecksumResult:
    checksum_sha256: str
    matched: bool


def _validated_checksum(
    *,
    content: bytes,
    source_checksum: str,
    source_id: str,
    collection: str,
    response: WorkspaceImportResponse,
    allow_replace: bool = False,
) -> _ChecksumResult:
    actual_checksum = sha256(content).hexdigest()
    matched = not source_checksum or source_checksum == actual_checksum
    if not matched and allow_replace:
        response.warnings.append(
            f"Imported {collection[:-1]} {source_id}: checksum replaced by resolution"
        )
        return _ChecksumResult(checksum_sha256=actual_checksum, matched=True)
    if not matched:
        response.warnings.append(
            f"Skipped {collection[:-1]} {source_id}: checksum mismatch"
        )
        response.conflict_plan.append(
            _checksum_conflict(
                collection=collection,
                source_id=source_id,
                source_checksum=source_checksum,
                actual_checksum=actual_checksum,
            )
        )
    return _ChecksumResult(checksum_sha256=actual_checksum, matched=matched)
