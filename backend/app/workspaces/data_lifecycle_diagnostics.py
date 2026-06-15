from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.api.services.workspace_export_constants import SUPPORTED_WORKSPACE_EXPORT_FORMAT
from backend.app.workspaces.data_lifecycle_payloads import (
    _archive_integrity_payload,
    _audit_event_payload,
    _job_payload,
)
from backend.app.workspaces.data_lifecycle_policy import (
    _backup_policy,
    _readiness,
    _retention_policy,
)
from backend.app.workspaces.data_lifecycle_recovery import (
    _backup_coverage,
    _restore_readiness,
)
from backend.app.workspaces.data_lifecycle_repository import WorkspaceDataLifecycleRepository
from backend.app.workspaces.data_lifecycle_schedule import (
    _automation_backup_warnings,
    _automation_restore_drill_warnings,
    _automation_retention_warnings,
    _backup_interval_hours,
    _restore_drill_due,
    _schedule_configured,
    _scheduled_backup_due,
)
from backend.app.workspaces.data_lifecycle_settings import (
    _restore_drill_settings,
    _retention_settings,
)
from backend.app.workspaces.models import Workspace

BACKUP_LIFECYCLE_EVENT_ACTIONS = (
    "workspace.lifecycle.backup_enqueued",
    "workspace.lifecycle.backup_skipped",
)
RETENTION_LIFECYCLE_EVENT_ACTIONS = (
    "workspace.lifecycle.retention_skipped",
    "workspace.retention_applied",
)
RESTORE_DRILL_LIFECYCLE_EVENT_ACTIONS = (
    "workspace.lifecycle.restore_drill_completed",
    "workspace.lifecycle.restore_drill_skipped",
)


class WorkspaceLifecycleDiagnosticsService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = WorkspaceDataLifecycleRepository(session)

    def get_diagnostics(self, *, workspace_id: UUID) -> dict[str, object] | None:
        workspace = self._session.get(Workspace, workspace_id)
        if workspace is None:
            return None

        generated_at = datetime.now(UTC)
        latest_job = self._repo._latest_export_job(workspace_id)
        latest_success = self._repo._latest_successful_archive_export(workspace_id)
        file_stats = self._repo._file_stats(workspace_id)
        artifact_stats = self._repo._artifact_stats(workspace_id)
        access_stats = self._repo._access_stats(workspace_id)
        retention_policy = _retention_policy(workspace.settings)
        backup_policy = _backup_policy(
            workspace.settings,
            latest_job,
            latest_success,
            generated_at=generated_at,
        )
        readiness = _readiness(retention_policy, backup_policy, latest_success)

        return {
            "workspace_id": workspace_id,
            "generated_at": generated_at,
            "export_import": {
                "format_version": SUPPORTED_WORKSPACE_EXPORT_FORMAT,
                "metadata_export_supported": True,
                "metadata_import_supported": True,
                "archive_export_supported": True,
                "archive_import_supported": True,
                "async_archive_export_supported": True,
                "supported_collections": [
                    "agents",
                    "teams",
                    "team_members",
                    "tasks",
                    "task_steps",
                    "task_messages",
                    "runs",
                    "run_events",
                    "files",
                    "artifacts",
                    "runtime_spaces",
                    "runtime_space_quotas",
                    "skill_installs",
                    "audit_events",
                ],
                "latest_export_job": _job_payload(latest_job),
                "latest_successful_archive_export": _job_payload(latest_success),
            },
            "backup_policy": backup_policy,
            "retention_policy": retention_policy,
            "storage": {
                "files": file_stats,
                "artifacts": artifact_stats,
                "total_bytes": file_stats["total_bytes"] + artifact_stats["total_bytes"],
            },
            "file_access_audit": access_stats,
            "automation": self._automation_diagnostics(
                workspace=workspace,
                backup_policy=backup_policy,
                retention_policy=retention_policy,
                generated_at=generated_at,
            ),
            "readiness": readiness,
        }

    def get_recovery_readiness(self, *, workspace_id: UUID) -> dict[str, object] | None:
        workspace = self._session.get(Workspace, workspace_id)
        if workspace is None:
            return None

        generated_at = datetime.now(UTC)
        latest_job = self._repo._latest_export_job(workspace_id)
        latest_success = self._repo._latest_successful_archive_export(workspace_id)
        latest_import = self._repo._latest_archive_import_event(workspace_id)
        latest_restore_drill = self._repo._latest_restore_drill_event(workspace_id)
        latest_integrity = self._repo._latest_archive_integrity_event(workspace_id)
        latest_failed_job = self._repo._latest_failed_export_job(workspace_id)
        job_stats = self._repo._export_job_stats(workspace_id)
        current_counts = self._repo._archive_coverage_counts(workspace_id)
        restore_test_history = self._repo._restore_test_history(
            workspace_id=workspace_id,
            latest_success=latest_success,
        )
        import_conflict_history = self._repo._import_conflict_history(workspace_id)
        retention_policy = _retention_policy(workspace.settings)
        backup_policy = _backup_policy(
            workspace.settings,
            latest_job,
            latest_success,
            generated_at=generated_at,
        )
        archive_integrity = _archive_integrity_payload(
            latest_integrity,
            latest_success=latest_success,
        )
        restore_readiness = _restore_readiness(
            latest_success=latest_success,
            backup_policy=backup_policy,
            generated_at=generated_at,
            active_job_count=job_stats["active_job_count"],
            backup_coverage=_backup_coverage(
                latest_success=latest_success,
                current_counts=current_counts,
            ),
            restore_test_history=restore_test_history,
            import_conflict_history=import_conflict_history,
            archive_integrity=archive_integrity,
        )

        return {
            "workspace_id": workspace_id,
            "generated_at": generated_at,
            "latest_successful_archive_export": _job_payload(latest_success),
            "latest_archive_import": _audit_event_payload(latest_import),
            "latest_restore_drill": _audit_event_payload(latest_restore_drill),
            "latest_failed_export_job": _job_payload(latest_failed_job),
            "export_jobs": job_stats,
            "archive_integrity": archive_integrity,
            "retention_safety": {
                "retention_enabled": retention_policy["enabled"],
                "backup_policy_enabled": backup_policy["enabled"],
                "retention_delete_policy": retention_policy["delete_policy"],
                "retention_requires_successful_backup_by_default": True,
                "protected_by_successful_archive": latest_success is not None,
                "warnings": [
                    *retention_policy["warnings"],
                    *backup_policy["warnings"],
                ],
            },
            "restore_readiness": restore_readiness,
        }

    def _automation_diagnostics(
        self,
        *,
        workspace: Workspace,
        backup_policy: dict[str, object],
        retention_policy: dict[str, object],
        generated_at: datetime,
    ) -> dict[str, object]:
        raw_retention = _retention_settings(workspace.settings)
        raw_restore_drill = _restore_drill_settings(workspace.settings)
        retention_interval_hours = _backup_interval_hours(raw_retention)
        restore_drill_interval_hours = _backup_interval_hours(raw_restore_drill)
        latest_retention_run_at = self._repo._latest_lifecycle_retention_run_at(workspace.id)
        latest_restore_drill = self._repo._latest_restore_drill_event(workspace.id)
        latest_restore_drill_at = (
            latest_restore_drill.created_at if latest_restore_drill is not None else None
        )
        latest_success = self._repo._latest_successful_archive_export(workspace.id)
        retention_next_due_at = (
            latest_retention_run_at + timedelta(hours=retention_interval_hours)
            if latest_retention_run_at is not None and retention_interval_hours is not None
            else None
        )
        restore_drill_next_due_at = (
            latest_restore_drill_at + timedelta(hours=restore_drill_interval_hours)
            if latest_restore_drill_at is not None
            and restore_drill_interval_hours is not None
            else None
        )
        active_archive_export_count = self._repo._active_archive_export_job_count(workspace.id)
        scheduled_backup_due = _scheduled_backup_due(backup_policy)
        scheduled_restore_drill_due = _restore_drill_due(
            raw_policy=raw_restore_drill,
            latest_success=latest_success,
            latest_drill=latest_restore_drill,
            generated_at=generated_at,
        )
        return {
            "scheduled_backup": {
                "enabled": backup_policy["enabled"],
                "configured": _schedule_configured(backup_policy),
                "due": scheduled_backup_due,
                "blocked_by_active_export": scheduled_backup_due
                and active_archive_export_count > 0,
                "active_archive_export_job_count": active_archive_export_count,
                "latest_scheduled_archive_export_job": _job_payload(
                    self._repo._latest_scheduled_archive_export_job(workspace.id)
                ),
                "latest_event": _audit_event_payload(
                    self._repo._latest_lifecycle_event(
                        workspace.id,
                        BACKUP_LIFECYCLE_EVENT_ACTIONS,
                    )
                ),
                "recent_events": [
                    _audit_event_payload(event)
                    for event in self._repo._recent_lifecycle_events(
                        workspace.id,
                        BACKUP_LIFECYCLE_EVENT_ACTIONS,
                    )
                ],
                "warnings": _automation_backup_warnings(
                    backup_policy,
                    active_archive_export_count=active_archive_export_count,
                ),
            },
            "scheduled_retention": {
                "enabled": retention_policy["enabled"],
                "auto_apply": raw_retention.get("auto_apply") is True,
                "configured": retention_interval_hours is not None,
                "interval_hours": retention_interval_hours,
                "latest_run_at": latest_retention_run_at,
                "next_due_at": retention_next_due_at,
                "due": bool(
                    raw_retention.get("auto_apply") is True
                    and retention_interval_hours is not None
                    and (
                        latest_retention_run_at is None
                        or (
                            retention_next_due_at is not None
                            and retention_next_due_at <= generated_at
                        )
                    )
                ),
                "latest_event": _audit_event_payload(
                    self._repo._latest_lifecycle_event(
                        workspace.id,
                        RETENTION_LIFECYCLE_EVENT_ACTIONS,
                    )
                ),
                "recent_events": [
                    _audit_event_payload(event)
                    for event in self._repo._recent_lifecycle_events(
                        workspace.id,
                        RETENTION_LIFECYCLE_EVENT_ACTIONS,
                    )
                ],
                "warnings": _automation_retention_warnings(
                    raw_retention=raw_retention,
                    retention_policy=retention_policy,
                    interval_hours=retention_interval_hours,
                ),
            },
            "scheduled_restore_drill": {
                "enabled": raw_restore_drill.get("enabled") is True,
                "configured": restore_drill_interval_hours is not None,
                "interval_hours": restore_drill_interval_hours,
                "latest_run_at": latest_restore_drill_at,
                "next_due_at": restore_drill_next_due_at,
                "due": scheduled_restore_drill_due,
                "blocked_by_active_export": scheduled_restore_drill_due
                and active_archive_export_count > 0,
                "latest_successful_archive_export_job_id": str(latest_success.id)
                if latest_success is not None
                else None,
                "latest_event": _audit_event_payload(
                    self._repo._latest_lifecycle_event(
                        workspace.id,
                        RESTORE_DRILL_LIFECYCLE_EVENT_ACTIONS,
                    )
                ),
                "recent_events": [
                    _audit_event_payload(event)
                    for event in self._repo._recent_lifecycle_events(
                        workspace.id,
                        RESTORE_DRILL_LIFECYCLE_EVENT_ACTIONS,
                    )
                ],
                "warnings": _automation_restore_drill_warnings(
                    raw_policy=raw_restore_drill,
                    interval_hours=restore_drill_interval_hours,
                    latest_success=latest_success,
                    latest_drill=latest_restore_drill,
                    due=scheduled_restore_drill_due,
                    active_archive_export_count=active_archive_export_count,
                ),
            },
        }
