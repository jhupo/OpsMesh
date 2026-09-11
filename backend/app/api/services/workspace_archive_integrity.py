from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from sqlalchemy.orm import Session

from backend.app.api.services.workspace_archive_export_repository import (
    WorkspaceArchiveExportJobRepository,
)
from backend.app.api.services.workspace_export_constants import SUPPORTED_WORKSPACE_EXPORT_FORMAT
from backend.app.observability.audit_service import AuditService
from backend.app.files.storage import ObjectStorage


class WorkspaceArchiveIntegrityService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._jobs = WorkspaceArchiveExportJobRepository(session)

    def verify_job(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        user_id: UUID,
        storage: ObjectStorage,
    ) -> dict[str, object]:
        export_job, content = self._jobs.read_completed_content(
            workspace_id=workspace_id,
            job_id=job_id,
            storage=storage,
        )
        checked_at = datetime.now(UTC)
        checks, metadata = inspect_archive_content(
            content=content,
            workspace_id=workspace_id,
            expected_size=export_job.size_bytes,
            expected_checksum=export_job.checksum_sha256,
            filename=export_job.filename,
            content_type=export_job.content_type,
        )
        failed_checks = [name for name, passed in checks.items() if not passed]
        verified = not failed_checks
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="workspace.archive_export_job.integrity_checked",
            target_type="workspace_export_job",
            target_id=export_job.id,
            metadata={
                "verified": verified,
                "checks": checks,
                "failed_checks": failed_checks,
                **metadata,
            },
        )
        self._session.commit()
        return {
            "workspace_id": workspace_id,
            "job_id": export_job.id,
            "verified": verified,
            "checked_at": checked_at,
            "checks": checks,
            "failed_checks": failed_checks,
            "metadata": metadata,
        }


def inspect_archive_content(
    *,
    content: bytes,
    workspace_id: UUID,
    expected_size: int | None,
    expected_checksum: str | None,
    filename: str | None,
    content_type: str | None,
) -> tuple[dict[str, bool], dict[str, object]]:
    actual_size = len(content)
    actual_checksum = sha256(content).hexdigest()
    checks = {
        "size_matches": expected_size == actual_size,
        "checksum_matches": expected_checksum == actual_checksum,
        "zip_readable": False,
        "metadata_present": False,
        "manifest_valid": False,
        "workspace_matches": False,
        "format_version_matches": False,
    }
    metadata: dict[str, object] = {
        "filename": filename,
        "content_type": content_type,
        "expected_size_bytes": expected_size,
        "actual_size_bytes": actual_size,
        "expected_checksum_sha256": expected_checksum,
        "actual_checksum_sha256": actual_checksum,
    }
    manifest_counts: dict[str, int] = {}
    try:
        with ZipFile(BytesIO(content)) as archive:
            checks["zip_readable"] = True
            names = set(archive.namelist())
            checks["metadata_present"] = "metadata.json" in names
            metadata["archive_entry_count"] = len(names)
            if checks["metadata_present"]:
                inspect_metadata_payload(
                    json.loads(archive.read("metadata.json")),
                    workspace_id,
                    checks,
                    lambda counts: manifest_counts.update(counts),
                )
    except (BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
        metadata["archive_error"] = str(exc)[:500]
    metadata["manifest_counts"] = manifest_counts
    return checks, metadata


def inspect_metadata_payload(
    payload: object,
    workspace_id: UUID,
    checks: dict[str, bool],
    record_counts: Callable[[dict[str, int]], None],
) -> None:
    manifest = payload.get("manifest") if isinstance(payload, dict) else None
    workspace = payload.get("workspace") if isinstance(payload, dict) else None
    if isinstance(manifest, dict):
        checks["format_version_matches"] = (
            manifest.get("format_version") == SUPPORTED_WORKSPACE_EXPORT_FORMAT
        )
        checks["workspace_matches"] = manifest.get("workspace_id") == str(workspace_id)
        raw_counts = manifest.get("counts")
        if isinstance(raw_counts, dict):
            record_counts(
                {
                    str(key): int(value)
                    for key, value in raw_counts.items()
                    if isinstance(value, int) and value >= 0
                }
            )
    elif isinstance(workspace, dict):
        checks["workspace_matches"] = workspace.get("id") == str(workspace_id)
    checks["manifest_valid"] = (
        checks["metadata_present"]
        and checks["format_version_matches"]
        and checks["workspace_matches"]
    )
