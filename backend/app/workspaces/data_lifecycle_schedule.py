from datetime import datetime, timedelta
from uuid import UUID

from backend.app.api.schemas.exports import (
    WorkspaceArchiveExportRequest,
    WorkspaceArchiveRestoreDrillRequest,
)
from backend.app.audit.models import AuditEvent
from backend.app.exports.models import WorkspaceExportJob
from backend.app.workspaces.data_lifecycle_settings import (
    _ensure_utc_datetime,
    _positive_int,
)
from backend.app.workspaces.data_lifecycle_summary import ScheduledLifecycleSummary


def _scheduled_archive_export_request(
    raw_policy: dict[str, object],
) -> WorkspaceArchiveExportRequest:
    raw_request = raw_policy.get("archive_request")
    if isinstance(raw_request, dict):
        return WorkspaceArchiveExportRequest.model_validate(raw_request)
    return WorkspaceArchiveExportRequest()


def _scheduled_restore_drill_request(
    raw_policy: dict[str, object],
) -> WorkspaceArchiveRestoreDrillRequest:
    raw_request = raw_policy.get("request")
    request: dict[str, object] = raw_request.copy() if isinstance(raw_request, dict) else {}
    for field_name in WorkspaceArchiveRestoreDrillRequest.model_fields:
        if field_name in raw_policy and field_name not in request:
            request[field_name] = raw_policy[field_name]
    return WorkspaceArchiveRestoreDrillRequest.model_validate(request)


def _scheduled_lifecycle_detail(
    workspace_id: UUID,
    stage: str,
    status: str,
    reason: str,
    *,
    resource_id: UUID | None = None,
) -> dict[str, object]:
    detail: dict[str, object] = {
        "workspace_id": str(workspace_id),
        "stage": stage,
        "status": status,
        "reason": reason,
    }
    if resource_id is not None:
        detail["resource_id"] = str(resource_id)
    return detail


def _restore_drill_skipped_summary(
    workspace_id: UUID,
    reason: str,
    *,
    resource_id: UUID | None = None,
) -> ScheduledLifecycleSummary:
    return ScheduledLifecycleSummary(
        restore_drills_skipped=1,
        details=[
            _scheduled_lifecycle_detail(
                workspace_id,
                "restore_drill",
                "skipped",
                reason,
                resource_id=resource_id,
            )
        ],
    )


def _scheduled_backup_due(backup_policy: dict[str, object]) -> bool:
    schedule_status = backup_policy.get("schedule_status")
    if not isinstance(schedule_status, dict):
        return False
    return bool(
        backup_policy["enabled"] is True
        and schedule_status.get("configured") is True
        and (
            schedule_status.get("last_successful_archive_export_at") is None
            or schedule_status.get("overdue") is True
        )
    )


def _restore_drill_due(
    *,
    raw_policy: dict[str, object],
    latest_success: WorkspaceExportJob | None,
    latest_drill: AuditEvent | None,
    generated_at: datetime,
) -> bool:
    interval_hours = _backup_interval_hours(raw_policy)
    if raw_policy.get("enabled") is not True or interval_hours is None:
        return False
    if latest_success is None:
        return True

    latest_success_at = _ensure_utc_datetime(latest_success.completed_at)
    latest_drill_at = _ensure_utc_datetime(
        latest_drill.created_at if latest_drill is not None else None
    )
    generated_at = _ensure_utc_datetime(generated_at) or generated_at
    if latest_drill_at is None:
        return True
    if latest_success_at is not None and latest_drill_at < latest_success_at:
        return True
    return latest_drill_at + timedelta(hours=interval_hours) <= generated_at


def _schedule_configured(policy: dict[str, object]) -> bool:
    schedule_status = policy.get("schedule_status")
    return bool(isinstance(schedule_status, dict) and schedule_status.get("configured") is True)


def _automation_backup_warnings(
    backup_policy: dict[str, object],
    *,
    active_archive_export_count: int,
) -> list[str]:
    warnings = (
        list(backup_policy["warnings"]) if isinstance(backup_policy["warnings"], list) else []
    )
    if _scheduled_backup_due(backup_policy) and active_archive_export_count > 0:
        warnings.append("scheduled_backup_waiting_for_active_export")
    return warnings


def _automation_retention_warnings(
    *,
    raw_retention: dict[str, object],
    retention_policy: dict[str, object],
    interval_hours: int | None,
) -> list[str]:
    warnings = (
        list(retention_policy["warnings"]) if isinstance(retention_policy["warnings"], list) else []
    )
    if raw_retention.get("auto_apply") is True and interval_hours is None:
        warnings.append("retention_schedule_unrecognized")
    return warnings


def _automation_restore_drill_warnings(
    *,
    raw_policy: dict[str, object],
    interval_hours: int | None,
    latest_success: WorkspaceExportJob | None,
    latest_drill: AuditEvent | None,
    due: bool,
    active_archive_export_count: int,
) -> list[str]:
    warnings: list[str] = []
    if raw_policy.get("enabled") is True and interval_hours is None:
        warnings.append("restore_drill_schedule_unrecognized")
    if due and latest_success is None:
        warnings.append("restore_drill_waiting_for_successful_archive")
    if due and active_archive_export_count > 0:
        warnings.append("restore_drill_waiting_for_active_export")
    metadata = (
        latest_drill.audit_metadata
        if latest_drill is not None and isinstance(latest_drill.audit_metadata, dict)
        else {}
    )
    if metadata.get("passed") is False:
        warnings.append("latest_restore_drill_failed")
    return warnings


def _backup_schedule_status(
    *,
    raw_policy: dict[str, object],
    enabled: bool,
    latest_success: WorkspaceExportJob | None,
    generated_at: datetime,
) -> dict[str, object]:
    schedule = raw_policy.get("schedule")
    interval_hours = _backup_interval_hours(raw_policy)
    last_success_at = _ensure_utc_datetime(
        latest_success.completed_at
        if latest_success is not None and latest_success.completed_at is not None
        else None
    )
    generated_at = _ensure_utc_datetime(generated_at) or generated_at
    next_due_at = (
        last_success_at + timedelta(hours=interval_hours)
        if last_success_at is not None and interval_hours is not None
        else None
    )
    overdue = bool(
        enabled
        and last_success_at is not None
        and next_due_at is not None
        and next_due_at <= generated_at
    )
    warnings: list[str] = []
    if enabled and schedule is not None and interval_hours is None:
        warnings.append("backup_schedule_unrecognized")
    if overdue:
        warnings.append("backup_schedule_overdue")
    return {
        "configured": bool(schedule is not None or interval_hours is not None),
        "schedule": schedule,
        "interval_hours": interval_hours,
        "last_successful_archive_export_at": last_success_at,
        "next_due_at": next_due_at,
        "overdue": overdue,
        "warnings": warnings,
    }


def _backup_interval_hours(raw_policy: dict[str, object]) -> int | None:
    explicit_interval = _positive_int(raw_policy.get("interval_hours"))
    if explicit_interval is not None:
        return explicit_interval
    schedule = raw_policy.get("schedule")
    if not isinstance(schedule, str):
        return None
    return {
        "hourly": 1,
        "daily": 24,
        "weekly": 168,
    }.get(schedule.strip().lower())
