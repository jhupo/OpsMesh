from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.services.exports import WorkspaceExportService
from backend.app.audit.models import AuditEvent
from backend.app.audit.service import AuditService
from backend.app.exports.models import WorkspaceExportJob
from backend.app.exports.status import WorkspaceExportJobStatus
from backend.app.files.storage import ObjectStorage
from backend.app.workers.queue.redis_queue import RedisQueue
from backend.app.workspaces.data_lifecycle_policy import _backup_policy
from backend.app.workspaces.data_lifecycle_retention import WorkspaceRetentionService
from backend.app.workspaces.data_lifecycle_schedule import (
    _backup_interval_hours,
    _restore_drill_due,
    _restore_drill_skipped_summary,
    _scheduled_archive_export_request,
    _scheduled_lifecycle_detail,
    _scheduled_restore_drill_request,
)
from backend.app.workspaces.data_lifecycle_settings import (
    _backup_settings,
    _bool_setting,
    _ensure_utc_datetime,
    _positive_int,
    _restore_drill_settings,
    _retention_settings,
)
from backend.app.workspaces.data_lifecycle_summary import ScheduledLifecycleSummary
from backend.app.workspaces.models import Workspace


class WorkspaceScheduledLifecycleService:
    def __init__(self, session: Session) -> None:
        self._session = session

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
        backup_summary = self._schedule_workspace_backup_if_due(workspace, queue)
        retention_summary = self._run_workspace_retention_if_due(workspace)
        restore_drill_summary = self._run_workspace_restore_drill_if_due(
            workspace,
            storage=storage,
        )
        return backup_summary.combine(retention_summary).combine(restore_drill_summary)

    def _schedule_workspace_backup_if_due(
        self,
        workspace: Workspace,
        queue: RedisQueue,
    ) -> ScheduledLifecycleSummary:
        raw_policy = _backup_settings(workspace.settings)
        latest_job = self._latest_export_job(workspace.id)
        latest_success = self._latest_successful_archive_export(workspace.id)
        backup_policy = _backup_policy(
            workspace.settings,
            latest_job,
            latest_success,
            generated_at=datetime.now(UTC),
        )
        schedule_status = backup_policy["schedule_status"]
        due = bool(
            backup_policy["enabled"] is True
            and isinstance(schedule_status, dict)
            and schedule_status["configured"] is True
            and (
                latest_success is None
                or schedule_status.get("overdue") is True
            )
        )
        if not due:
            return ScheduledLifecycleSummary()

        if self._has_active_archive_export_job(workspace.id):
            self._record_lifecycle_schedule_event(
                workspace=workspace,
                action="workspace.lifecycle.backup_skipped",
                reason="archive_export_already_active",
                metadata={"schedule_status": schedule_status},
            )
            self._session.commit()
            return ScheduledLifecycleSummary(
                backup_jobs_skipped=1,
                details=[
                    _scheduled_lifecycle_detail(
                        workspace.id,
                        "backup",
                        "skipped",
                        "archive_export_already_active",
                    )
                ],
            )

        try:
            request = _scheduled_archive_export_request(raw_policy)
        except ValidationError as exc:
            self._record_lifecycle_schedule_event(
                workspace=workspace,
                action="workspace.lifecycle.backup_skipped",
                reason="invalid_archive_request",
                metadata={"error": str(exc)[:1000], "schedule_status": schedule_status},
            )
            self._session.commit()
            return ScheduledLifecycleSummary(
                backup_jobs_skipped=1,
                details=[
                    _scheduled_lifecycle_detail(
                        workspace.id,
                        "backup",
                        "skipped",
                        "invalid_archive_request",
                    )
                ],
            )

        export_job = WorkspaceExportService(self._session).create_archive_export_job(
            workspace=workspace,
            user_id=workspace.owner_user_id,
            request=request,
            queue=queue,
        )
        export_job.job_metadata = {
            **export_job.job_metadata,
            "scheduled_by": "workspace_data_lifecycle",
            "schedule_status": schedule_status,
        }
        self._record_lifecycle_schedule_event(
            workspace=workspace,
            action="workspace.lifecycle.backup_enqueued",
            reason="backup_schedule_due",
            metadata={
                "export_job_id": str(export_job.id),
                "schedule_status": schedule_status,
            },
        )
        self._session.commit()
        return ScheduledLifecycleSummary(
            backup_jobs_enqueued=1,
            details=[
                _scheduled_lifecycle_detail(
                    workspace.id,
                    "backup",
                    "enqueued",
                    "backup_schedule_due",
                    resource_id=export_job.id,
                )
            ],
        )

    def _run_workspace_retention_if_due(
        self,
        workspace: Workspace,
    ) -> ScheduledLifecycleSummary:
        raw_policy = _retention_settings(workspace.settings)
        if raw_policy.get("auto_apply") is not True:
            return ScheduledLifecycleSummary()

        interval_hours = _backup_interval_hours(raw_policy)
        if interval_hours is None:
            self._record_lifecycle_schedule_event(
                workspace=workspace,
                action="workspace.lifecycle.retention_skipped",
                reason="retention_schedule_unrecognized",
                metadata={"schedule": raw_policy.get("schedule")},
            )
            self._session.commit()
            return ScheduledLifecycleSummary(
                retention_runs_skipped=1,
                details=[
                    _scheduled_lifecycle_detail(
                        workspace.id,
                        "retention",
                        "skipped",
                        "retention_schedule_unrecognized",
                    )
                ],
            )

        latest_run_at = self._latest_lifecycle_retention_run_at(workspace.id)
        now = datetime.now(UTC)
        if latest_run_at is not None and latest_run_at + timedelta(hours=interval_hours) > now:
            return ScheduledLifecycleSummary()

        response = WorkspaceRetentionService(self._session).apply_retention(
            workspace_id=workspace.id,
            user_id=workspace.owner_user_id,
            include_files=_bool_setting(raw_policy, "include_files", True),
            include_export_jobs=_bool_setting(raw_policy, "include_export_jobs", True),
            include_artifacts=_bool_setting(raw_policy, "include_artifacts", True),
            max_items=_positive_int(raw_policy.get("max_items")) or 100,
            require_successful_backup=_bool_setting(
                raw_policy,
                "require_successful_backup",
                True,
            ),
        )
        if response is None or response["blocked_reasons"]:
            blocked_reasons = (
                response["blocked_reasons"]
                if response is not None and isinstance(response["blocked_reasons"], list)
                else ["retention_response_missing"]
            )
            self._record_lifecycle_schedule_event(
                workspace=workspace,
                action="workspace.lifecycle.retention_skipped",
                reason="retention_blocked",
                metadata={"blocked_reasons": blocked_reasons},
            )
            self._session.commit()
            return ScheduledLifecycleSummary(
                retention_runs_skipped=1,
                details=[
                    _scheduled_lifecycle_detail(
                        workspace.id,
                        "retention",
                        "skipped",
                        "retention_blocked",
                    )
                ],
            )
        return ScheduledLifecycleSummary(
            retention_runs_applied=1,
            details=[
                _scheduled_lifecycle_detail(
                    workspace.id,
                    "retention",
                    "applied",
                    "retention_schedule_due",
                )
            ],
        )

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
            self._record_lifecycle_schedule_event(
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

        latest_success = self._latest_successful_archive_export(workspace.id)
        latest_drill = self._latest_restore_drill_event(workspace.id)
        now = datetime.now(UTC)
        if not _restore_drill_due(
            raw_policy=raw_policy,
            latest_success=latest_success,
            latest_drill=latest_drill,
            generated_at=now,
        ):
            return ScheduledLifecycleSummary()

        if latest_success is None:
            self._record_lifecycle_schedule_event(
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

        if self._has_active_archive_export_job(workspace.id):
            self._record_lifecycle_schedule_event(
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
            self._record_lifecycle_schedule_event(
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
            self._record_lifecycle_schedule_event(
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
            self._record_lifecycle_schedule_event(
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

        self._record_lifecycle_schedule_event(
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



    def _has_active_archive_export_job(self, workspace_id: UUID) -> bool:
        return self._active_archive_export_job_count(workspace_id) > 0

    def _active_archive_export_job_count(self, workspace_id: UUID) -> int:
        active_count = self._session.scalar(
            select(func.count(WorkspaceExportJob.id)).where(
                WorkspaceExportJob.workspace_id == workspace_id,
                WorkspaceExportJob.export_type == "workspace_archive",
                WorkspaceExportJob.status.in_(
                    [
                        WorkspaceExportJobStatus.QUEUED.value,
                        WorkspaceExportJobStatus.RUNNING.value,
                    ]
                ),
            )
        )
        return int(active_count or 0)

    def _latest_lifecycle_retention_run_at(self, workspace_id: UUID) -> datetime | None:
        latest = self._session.scalar(
            select(func.max(AuditEvent.created_at)).where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action == "workspace.retention_applied",
            )
        )
        return _ensure_utc_datetime(latest)

    def _record_lifecycle_schedule_event(
        self,
        *,
        workspace: Workspace,
        action: str,
        reason: str,
        metadata: dict[str, object],
    ) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=workspace.owner_user_id,
            action=action,
            target_type="workspace",
            target_id=workspace.id,
            metadata={"reason": reason, **metadata},
        )

    def _latest_export_job(self, workspace_id: UUID) -> WorkspaceExportJob | None:
        return self._session.scalar(
            select(WorkspaceExportJob)
            .where(WorkspaceExportJob.workspace_id == workspace_id)
            .order_by(WorkspaceExportJob.created_at.desc(), WorkspaceExportJob.id.desc())
            .limit(1)
        )

    def _latest_successful_archive_export(self, workspace_id: UUID) -> WorkspaceExportJob | None:
        return self._session.scalar(
            select(WorkspaceExportJob)
            .where(
                WorkspaceExportJob.workspace_id == workspace_id,
                WorkspaceExportJob.export_type == "workspace_archive",
                WorkspaceExportJob.status == WorkspaceExportJobStatus.COMPLETED.value,
            )
            .order_by(WorkspaceExportJob.completed_at.desc(), WorkspaceExportJob.id.desc())
            .limit(1)
        )

    def _latest_restore_drill_event(self, workspace_id: UUID) -> AuditEvent | None:
        return self._session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action == "workspace.archive_restore_drill.completed",
            )
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(1)
        )
