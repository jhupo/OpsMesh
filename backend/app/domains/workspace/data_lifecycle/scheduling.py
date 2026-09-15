from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domains.workspace.data_lifecycle.settings import (
    _ensure_utc_datetime,
    _positive_int,
)
from backend.app.domains.workspace.data_transfer.contracts import (
    WorkspaceArchiveExportRequest,
    WorkspaceArchiveRestoreDrillRequest,
)
from backend.app.domains.workspace.data_transfer.models import WorkspaceExportJob
from backend.app.domains.workspace.storage.storage import ObjectStorage
from backend.app.domains.workspace.tenants.models import Workspace
from backend.app.observability.audit.models import AuditEvent
from backend.app.runtime.workers.queue import RedisQueue


def scheduled_archive_export_request(
    raw_policy: dict[str, object],
) -> WorkspaceArchiveExportRequest:
    raw_request = raw_policy.get("archive_request")
    if isinstance(raw_request, dict):
        return WorkspaceArchiveExportRequest.model_validate(raw_request)
    return WorkspaceArchiveExportRequest()


def scheduled_restore_drill_request(
    raw_policy: dict[str, object],
) -> WorkspaceArchiveRestoreDrillRequest:
    raw_request = raw_policy.get("request")
    request: dict[str, object] = raw_request.copy() if isinstance(raw_request, dict) else {}
    for field_name in WorkspaceArchiveRestoreDrillRequest.model_fields:
        if field_name in raw_policy and field_name not in request:
            request[field_name] = raw_policy[field_name]
    return WorkspaceArchiveRestoreDrillRequest.model_validate(request)


def scheduled_lifecycle_detail(
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


def restore_drill_skipped_summary(
    workspace_id: UUID,
    reason: str,
    *,
    resource_id: UUID | None = None,
) -> ScheduledLifecycleSummary:
    return ScheduledLifecycleSummary(
        restore_drills_skipped=1,
        details=[
            scheduled_lifecycle_detail(
                workspace_id,
                "restore_drill",
                "skipped",
                reason,
                resource_id=resource_id,
            )
        ],
    )


def scheduled_backup_due(backup_policy: dict[str, object]) -> bool:
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


def restore_drill_due(
    *,
    raw_policy: dict[str, object],
    latest_success: WorkspaceExportJob | None,
    latest_drill: AuditEvent | None,
    generated_at: datetime,
) -> bool:
    interval_hours = backup_interval_hours(raw_policy)
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


def schedule_configured(policy: dict[str, object]) -> bool:
    schedule_status = policy.get("schedule_status")
    return bool(isinstance(schedule_status, dict) and schedule_status.get("configured") is True)


def automation_backup_warnings(
    backup_policy: dict[str, object],
    *,
    active_archive_export_count: int,
) -> list[str]:
    warnings = (
        list(backup_policy["warnings"]) if isinstance(backup_policy["warnings"], list) else []
    )
    if scheduled_backup_due(backup_policy) and active_archive_export_count > 0:
        warnings.append("scheduled_backup_waiting_for_active_export")
    return warnings


def automation_retention_warnings(
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


def automation_restore_drill_warnings(
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


def backup_schedule_status(
    *,
    raw_policy: dict[str, object],
    enabled: bool,
    latest_success: WorkspaceExportJob | None,
    generated_at: datetime,
) -> dict[str, object]:
    schedule = raw_policy.get("schedule")
    interval_hours = backup_interval_hours(raw_policy)
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


def backup_interval_hours(raw_policy: dict[str, object]) -> int | None:
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

class WorkspaceScheduledLifecycleService:
    def __init__(self, session: Session) -> None:
        from backend.app.domains.workspace.data_lifecycle.scheduled_backup import (
            ScheduledBackupService,
        )
        from backend.app.domains.workspace.data_lifecycle.scheduled_restore import (
            ScheduledRestoreDrillService,
        )
        from backend.app.domains.workspace.data_lifecycle.scheduled_retention import (
            ScheduledRetentionService,
        )

        self._session = session
        self._backup = ScheduledBackupService(session)
        self._retention = ScheduledRetentionService(session)
        self._restore_drill = ScheduledRestoreDrillService(session)

    def run_scheduled_lifecycle(
        self,
        *,
        queue: RedisQueue,
        storage: ObjectStorage | None = None,
        workspace_id: UUID | None = None,
        limit: int = 100,
    ) -> ScheduledLifecycleSummary:
        statement = select(Workspace).where(Workspace.status == "active")
        if workspace_id is not None:
            statement = statement.where(Workspace.id == workspace_id)
        workspaces = self._session.scalars(
            statement.order_by(Workspace.created_at.asc(), Workspace.id.asc()).limit(limit)
        ).all()

        summary = ScheduledLifecycleSummary(scanned_workspaces=len(workspaces))
        for workspace in workspaces:
            summary = summary.combine(
                self._run_workspace_scheduled_lifecycle(
                    workspace,
                    queue,
                    storage=storage,
                )
            )
        return summary

    def _run_workspace_scheduled_lifecycle(
        self,
        workspace: Workspace,
        queue: RedisQueue,
        *,
        storage: ObjectStorage | None,
    ) -> ScheduledLifecycleSummary:
        backup_summary = self._backup.run_if_due(workspace, queue)
        retention_summary = self._retention.run_if_due(workspace)
        restore_drill_summary = self._restore_drill.run_if_due(
            workspace,
            storage=storage,
        )
        return backup_summary.combine(retention_summary).combine(restore_drill_summary)

@dataclass(frozen=True)
class ScheduledLifecycleSummary:
    scanned_workspaces: int = 0
    backup_jobs_enqueued: int = 0
    backup_jobs_skipped: int = 0
    retention_runs_applied: int = 0
    retention_runs_skipped: int = 0
    restore_drills_completed: int = 0
    restore_drills_skipped: int = 0
    details: list[dict[str, object]] = field(default_factory=list)

    def combine(self, other: ScheduledLifecycleSummary) -> ScheduledLifecycleSummary:
        return ScheduledLifecycleSummary(
            scanned_workspaces=self.scanned_workspaces + other.scanned_workspaces,
            backup_jobs_enqueued=self.backup_jobs_enqueued + other.backup_jobs_enqueued,
            backup_jobs_skipped=self.backup_jobs_skipped + other.backup_jobs_skipped,
            retention_runs_applied=self.retention_runs_applied + other.retention_runs_applied,
            retention_runs_skipped=self.retention_runs_skipped + other.retention_runs_skipped,
            restore_drills_completed=(
                self.restore_drills_completed + other.restore_drills_completed
            ),
            restore_drills_skipped=self.restore_drills_skipped + other.restore_drills_skipped,
            details=[*self.details, *other.details],
        )

    def to_metadata(self) -> dict[str, object]:
        return {
            "scanned_workspaces": self.scanned_workspaces,
            "backup_jobs_enqueued": self.backup_jobs_enqueued,
            "backup_jobs_skipped": self.backup_jobs_skipped,
            "retention_runs_applied": self.retention_runs_applied,
            "retention_runs_skipped": self.retention_runs_skipped,
            "restore_drills_completed": self.restore_drills_completed,
            "restore_drills_skipped": self.restore_drills_skipped,
            "details": self.details,
        }
