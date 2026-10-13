"""Scheduled workspace restore-drill execution."""

from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy.orm import Session

from opsmesh.resources.lifecycle.repository import WorkspaceDataLifecycleRepository
from opsmesh.resources.lifecycle.scheduling import (
    ScheduledLifecycleSummary,
    backup_interval_hours,
    restore_drill_due,
    restore_drill_skipped_summary,
    scheduled_lifecycle_detail,
    scheduled_restore_drill_request,
)
from opsmesh.resources.lifecycle.settings import _restore_drill_settings
from opsmesh.resources.transfers.restore_operations import RestoreDrillOperationService
from opsmesh.shared.errors import DomainError
from opsmesh.workspaces.management.models import Workspace


class ScheduledRestoreDrillService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = WorkspaceDataLifecycleRepository(session)

    def run_if_due(
        self,
        workspace: Workspace,
    ) -> ScheduledLifecycleSummary:
        raw_policy = _restore_drill_settings(workspace.settings)
        if raw_policy.get("enabled") is not True:
            return ScheduledLifecycleSummary()

        interval_hours = backup_interval_hours(raw_policy)
        if interval_hours is None:
            self._repository.record_lifecycle_schedule_event(
                workspace=workspace,
                action="workspace.lifecycle.restore_drill_skipped",
                reason="restore_drill_schedule_unrecognized",
                metadata={"schedule": raw_policy.get("schedule")},
            )
            self._session.commit()
            return restore_drill_skipped_summary(
                workspace.id,
                "restore_drill_schedule_unrecognized",
            )

        latest_success = self._repository.latest_successful_archive_export(workspace.id)
        latest_drill = self._repository.latest_restore_drill_event(workspace.id)
        now = datetime.now(UTC)
        if not restore_drill_due(
            raw_policy=raw_policy,
            latest_success=latest_success,
            latest_drill=latest_drill,
            generated_at=now,
        ):
            return ScheduledLifecycleSummary()

        if latest_success is None:
            self._repository.record_lifecycle_schedule_event(
                workspace=workspace,
                action="workspace.lifecycle.restore_drill_skipped",
                reason="no_successful_archive_export",
                metadata={},
            )
            self._session.commit()
            return restore_drill_skipped_summary(
                workspace.id,
                "no_successful_archive_export",
            )

        if self._repository.has_active_archive_export_job(workspace.id):
            self._repository.record_lifecycle_schedule_event(
                workspace=workspace,
                action="workspace.lifecycle.restore_drill_skipped",
                reason="archive_export_already_active",
                metadata={"source_export_job_id": str(latest_success.id)},
            )
            self._session.commit()
            return restore_drill_skipped_summary(
                workspace.id,
                "archive_export_already_active",
                resource_id=latest_success.id,
            )

        try:
            request = scheduled_restore_drill_request(raw_policy)
        except ValidationError as exc:
            self._repository.record_lifecycle_schedule_event(
                workspace=workspace,
                action="workspace.lifecycle.restore_drill_skipped",
                reason="invalid_restore_drill_request",
                metadata={
                    "source_export_job_id": str(latest_success.id),
                    "error": str(exc)[:1000],
                },
            )
            self._session.commit()
            return restore_drill_skipped_summary(
                workspace.id,
                "invalid_restore_drill_request",
                resource_id=latest_success.id,
            )

        try:
            result = RestoreDrillOperationService(self._session).create(
                workspace.id,
                latest_success.id,
                workspace.owner_user_id,
                request,
                origin="scheduled",
            )
        except (FileNotFoundError, ValueError, DomainError) as exc:
            self._repository.record_lifecycle_schedule_event(
                workspace=workspace,
                action="workspace.lifecycle.restore_drill_skipped",
                reason="restore_drill_failed",
                metadata={
                    "source_export_job_id": str(latest_success.id),
                    "error_type": exc.__class__.__name__,
                },
            )
            self._session.commit()
            return restore_drill_skipped_summary(
                workspace.id,
                "restore_drill_failed",
                resource_id=latest_success.id,
            )

        self._repository.record_lifecycle_schedule_event(
            workspace=workspace,
            action="workspace.lifecycle.restore_drill_queued",
            reason="restore_drill_schedule_due",
            metadata={
                "source_export_job_id": str(latest_success.id),
                "operation_id": str(result.id),
            },
        )
        self._session.commit()
        return ScheduledLifecycleSummary(
            restore_drills_queued=1,
            details=[
                scheduled_lifecycle_detail(
                    workspace.id,
                    "restore_drill",
                    "queued",
                    "restore_drill_schedule_due",
                    resource_id=latest_success.id,
                )
            ],
        )
