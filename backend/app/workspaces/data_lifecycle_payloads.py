from backend.app.audit.models import AuditEvent
from backend.app.exports.models import WorkspaceExportJob
from backend.app.workspaces.data_lifecycle_settings import (
    _safe_conflict_summaries,
    _safe_count_map,
    _safe_int,
    _string_list,
)


def _restore_test_payload(event: AuditEvent) -> dict[str, object]:
    metadata = event.audit_metadata if isinstance(event.audit_metadata, dict) else {}
    return {
        "id": event.id,
        "action": event.action,
        "created_at": event.created_at,
        "user_id": event.user_id,
        "source_workspace_id": metadata.get("source_workspace_id"),
        "source_export_job_id": metadata.get("source_export_job_id"),
        "passed": metadata.get("passed"),
        "created_counts": _metadata_counts(event, "created_counts"),
        "skipped_counts": _metadata_counts(event, "skipped_counts"),
    }


def _import_preview_payload(event: AuditEvent | None) -> dict[str, object] | None:
    if event is None:
        return None
    metadata = event.audit_metadata if isinstance(event.audit_metadata, dict) else {}
    return {
        "id": event.id,
        "action": event.action,
        "created_at": event.created_at,
        "user_id": event.user_id,
        "source_workspace_id": metadata.get("source_workspace_id"),
        "created_counts": _safe_count_map(metadata.get("created_counts")),
        "skipped_counts": _safe_count_map(metadata.get("skipped_counts")),
        "conflict_counts": _safe_count_map(metadata.get("conflict_counts")),
        "conflict_severity_counts": _safe_count_map(
            metadata.get("conflict_severity_counts")
        ),
        "conflict_strategy_counts": _safe_count_map(
            metadata.get("conflict_strategy_counts")
        ),
        "required_resolution_count": _safe_int(
            metadata.get("required_resolution_count")
        ),
        "suggested_resolution_count": _safe_int(
            metadata.get("suggested_resolution_count")
        ),
        "conflict_summaries": _safe_conflict_summaries(
            metadata.get("conflict_summaries")
        ),
    }


def _archive_integrity_payload(
    event: AuditEvent | None,
    *,
    latest_success: WorkspaceExportJob | None,
) -> dict[str, object]:
    metadata = (
        event.audit_metadata
        if event is not None and isinstance(event.audit_metadata, dict)
        else {}
    )
    latest_success_id = str(latest_success.id) if latest_success is not None else None
    checked_job_id = event.target_id if event is not None else None
    return {
        "latest_check": _audit_event_payload(event),
        "latest_check_verified": metadata.get("verified") if event is not None else None,
        "latest_check_failed_checks": _string_list(metadata.get("failed_checks")),
        "latest_check_job_id": checked_job_id,
        "latest_successful_archive_export_job_id": latest_success_id,
        "latest_check_covers_latest_successful_archive": (
            event is not None
            and latest_success_id is not None
            and checked_job_id == latest_success_id
        ),
    }


def _metadata_counts(event: AuditEvent | None, key: str) -> dict[str, int]:
    if event is None:
        return {}
    metadata = event.audit_metadata if isinstance(event.audit_metadata, dict) else {}
    return _safe_count_map(metadata.get(key))


def _job_payload(job: WorkspaceExportJob | None) -> dict[str, object] | None:
    if job is None:
        return None
    return {
        "id": job.id,
        "export_type": job.export_type,
        "status": job.status,
        "filename": job.filename,
        "content_type": job.content_type,
        "size_bytes": job.size_bytes,
        "checksum_sha256": job.checksum_sha256,
        "error": job.error,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "created_at": job.created_at,
        "has_storage_object": job.storage_key is not None,
        "request": job.request,
        "metadata": job.job_metadata,
    }


def _audit_event_payload(event: AuditEvent | None) -> dict[str, object] | None:
    if event is None:
        return None
    return {
        "id": event.id,
        "action": event.action,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "user_id": event.user_id,
        "metadata": event.audit_metadata,
        "created_at": event.created_at,
    }
