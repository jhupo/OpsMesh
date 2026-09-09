from datetime import UTC, datetime

from pydantic import ValidationError

from backend.app.api.services.exports import WorkspaceExportService
from backend.app.files.storage import ObjectStorage
from backend.app.workspaces.data_lifecycle_schedule import (
    _backup_interval_hours,
    _restore_drill_due,
    _restore_drill_skipped_summary,
    _scheduled_lifecycle_detail,
    _scheduled_restore_drill_request,
)
from backend.app.workspaces.data_lifecycle_settings import _restore_drill_settings
from backend.app.workspaces.data_lifecycle_store import LifecycleStore
from backend.app.workspaces.data_lifecycle_summary import ScheduledLifecycleSummary
from backend.app.workspaces.models import Workspace


class ScheduledRestoreDrillMixin(LifecycleStore):
    def _run_workspace_restore_drill_if_due(
        self,
        workspace: Workspace,
        *,
        storage: ObjectStorage | None,
    ) -> ScheduledLifecycleSummary:
        raw_policy = _restore_drill_settings(workspace.settings)
        if raw_policy.get("enabled") is not True:
            return ScheduledLifecycleSummary()

        interval_hours = _backup_interval_hours(raw_policy)
        if interval_hours is None:
            self._repo.record_lifecycle_schedule_event(
                workspace=workspace,
                action="workspace.lifecycle.restore_drill_skipped",
                reason="restore_drill_schedule_unrecognized",
                metadata={"schedule": raw_policy.get("schedule")},
            )
            self._session.commit()
            return _restore_drill_skipped_summary(
                workspace.id,
                "restore_drill_schedule_unrecognized",
            )

        latest_success = self._repo.latest_successful_archive_export(workspace.id)
        latest_drill = self._repo.latest_restore_drill_event(workspace.id)
        now = datetime.now(UTC)
        if not _restore_drill_due(
            raw_policy=raw_policy,
            latest_success=latest_success,
            latest_drill=latest_drill,
            generated_at=now,
        ):
            return ScheduledLifecycleSummary()

        if latest_success is None:
            self._repo.record_lifecycle_schedule_event(
                workspace=workspace,
                action="workspace.lifecycle.restore_drill_skipped",
                reason="no_successful_archive_export",
                metadata={},
            )
            self._session.commit()
            return _restore_drill_skipped_summary(
                workspace.id,
                "no_successful_archive_export",
            )

        if self._repo.has_active_archive_export_job(workspace.id):
            self._repo.record_lifecycle_schedule_event(
                workspace=workspace,
                action="workspace.lifecycle.restore_drill_skipped",
                reason="archive_export_already_active",
                metadata={"source_export_job_id": str(latest_success.id)},
            )
            self._session.commit()
            return _restore_drill_skipped_summary(
                workspace.id,
                "archive_export_already_active",
                resource_id=latest_success.id,
            )

        if storage is None:
            self._repo.record_lifecycle_schedule_event(
                workspace=workspace,
                action="workspace.lifecycle.restore_drill_skipped",
                reason="storage_unavailable",
                metadata={"source_export_job_id": str(latest_success.id)},
            )
            self._session.commit()
            return _restore_drill_skipped_summary(
                workspace.id,
                "storage_unavailable",
                resource_id=latest_success.id,
            )

        try:
            request = _scheduled_restore_drill_request(raw_policy)
        except ValidationError as exc:
            self._repo.record_lifecycle_schedule_event(
                workspace=workspace,
                action="workspace.lifecycle.restore_drill_skipped",
                reason="invalid_restore_drill_request",
                metadata={
                    "source_export_job_id": str(latest_success.id),
                    "error": str(exc)[:1000],
                },
            )
            self._session.commit()
            return _restore_drill_skipped_summary(
                workspace.id,
                "invalid_restore_drill_request",
                resource_id=latest_success.id,
            )

        try:
            result = WorkspaceExportService(self._session).run_archive_restore_drill(
                workspace=workspace,
                user_id=workspace.owner_user_id,
                job_id=latest_success.id,
                request=request,
                storage=storage,
            )
        except (FileNotFoundError, ValueError) as exc:
            self._repo.record_lifecycle_schedule_event(
                workspace=workspace,
                action="workspace.lifecycle.restore_drill_skipped",
                reason="restore_drill_failed",
                metadata={
                    "source_export_job_id": str(latest_success.id),
                    "error_type": exc.__class__.__name__,
                },
            )
            self._session.commit()
            return _restore_drill_skipped_summary(
                workspace.id,
                "restore_drill_failed",
                resource_id=latest_success.id,
            )

        self._repo.record_lifecycle_schedule_event(
            workspace=workspace,
            action="workspace.lifecycle.restore_drill_completed",
            reason="restore_drill_schedule_due",
            metadata={
                "source_export_job_id": str(latest_success.id),
                "passed": result["passed"],
                "required_resolution_count": result["required_resolution_count"],
                "suggested_resolution_count": result["suggested_resolution_count"],
                "conflict_counts": result["conflict_counts"],
            },
        )
        self._session.commit()
        return ScheduledLifecycleSummary(
            restore_drills_completed=1,
            details=[
                _scheduled_lifecycle_detail(
                    workspace.id,
                    "restore_drill",
                    "completed",
                    "restore_drill_schedule_due",
                    resource_id=latest_success.id,
                )
            ],
        )
