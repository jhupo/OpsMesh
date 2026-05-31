from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.schemas.exports import (
    WorkspaceArchiveExportRequest,
    WorkspaceArchiveRestoreDrillRequest,
)
from backend.app.api.services.exports import (
    SUPPORTED_WORKSPACE_EXPORT_FORMAT,
    WorkspaceExportService,
)
from backend.app.artifacts.models import Artifact
from backend.app.audit.models import AuditEvent
from backend.app.audit.service import AuditService
from backend.app.capabilities.models import WorkspaceSkillInstall
from backend.app.exports.models import WorkspaceExportJob
from backend.app.exports.status import WorkspaceExportJobStatus
from backend.app.files.models import FileAccessEvent, WorkspaceFile
from backend.app.files.storage import LocalStorage
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceQuota
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workers.queue import RedisQueue
from backend.app.workspaces.models import Workspace

RETENTION_DELETED_FILE_STATUS = "retention_deleted"
ARCHIVE_COVERAGE_MODELS = {
    "agents": AgentProfile,
    "teams": AgentTeam,
    "team_members": AgentTeamMember,
    "tasks": Task,
    "task_steps": TaskStep,
    "task_messages": TaskMessage,
    "runs": AgentRun,
    "run_events": RunEvent,
    "files": WorkspaceFile,
    "artifacts": Artifact,
    "runtime_spaces": RuntimeSpace,
    "runtime_space_quotas": RuntimeSpaceQuota,
    "skill_installs": WorkspaceSkillInstall,
}
_BACKUP_LIFECYCLE_EVENT_ACTIONS = (
    "workspace.lifecycle.backup_enqueued",
    "workspace.lifecycle.backup_skipped",
)
_RETENTION_LIFECYCLE_EVENT_ACTIONS = (
    "workspace.lifecycle.retention_skipped",
    "workspace.retention_applied",
)
_RESTORE_DRILL_LIFECYCLE_EVENT_ACTIONS = (
    "workspace.lifecycle.restore_drill_completed",
    "workspace.lifecycle.restore_drill_skipped",
)
_RESTORE_TEST_EVENT_ACTIONS = (
    "workspace.archive_import.created",
    "workspace.archive_restore_drill.completed",
)
RECOVERY_READINESS_APPLY_ACTIONS = frozenset(
    {"run_archive_export", "run_restore_import_test"}
)


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


class WorkspaceDataLifecycleService:
    """Diagnose workspace export, backup, retention, and access-audit readiness."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_diagnostics(self, *, workspace_id: UUID) -> dict[str, object] | None:
        workspace = self._session.get(Workspace, workspace_id)
        if workspace is None:
            return None

        generated_at = datetime.now(UTC)
        latest_job = self._latest_export_job(workspace_id)
        latest_success = self._latest_successful_archive_export(workspace_id)
        file_stats = self._file_stats(workspace_id)
        artifact_stats = self._artifact_stats(workspace_id)
        access_stats = self._access_stats(workspace_id)
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
        latest_job = self._latest_export_job(workspace_id)
        latest_success = self._latest_successful_archive_export(workspace_id)
        latest_import = self._latest_archive_import_event(workspace_id)
        latest_restore_drill = self._latest_restore_drill_event(workspace_id)
        latest_integrity = self._latest_archive_integrity_event(workspace_id)
        latest_failed_job = self._latest_failed_export_job(workspace_id)
        job_stats = self._export_job_stats(workspace_id)
        current_counts = self._archive_coverage_counts(workspace_id)
        restore_test_history = self._restore_test_history(
            workspace_id=workspace_id,
            latest_success=latest_success,
        )
        import_conflict_history = self._import_conflict_history(workspace_id)
        retention_policy = _retention_policy(workspace.settings)
        backup_policy = _backup_policy(
            workspace.settings,
            latest_job,
            latest_success,
            generated_at=generated_at,
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
        )

        return {
            "workspace_id": workspace_id,
            "generated_at": generated_at,
            "latest_successful_archive_export": _job_payload(latest_success),
            "latest_archive_import": _audit_event_payload(latest_import),
            "latest_restore_drill": _audit_event_payload(latest_restore_drill),
            "latest_failed_export_job": _job_payload(latest_failed_job),
            "export_jobs": job_stats,
            "archive_integrity": _archive_integrity_payload(
                latest_integrity,
                latest_success=latest_success,
            ),
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

    def apply_recovery_readiness_actions(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        queue: RedisQueue,
        storage: LocalStorage | None = None,
        dry_run: bool = True,
        actions: list[str] | None = None,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        workspace = self._session.get(Workspace, workspace_id)
        if workspace is None:
            return None

        readiness = self.get_recovery_readiness(workspace_id=workspace_id)
        if readiness is None:
            return None
        restore_readiness = readiness["restore_readiness"]
        if not isinstance(restore_readiness, dict):
            restore_readiness = {}
        recommended_actions = _string_list(restore_readiness.get("recommended_actions"))
        requested_actions = _recovery_readiness_actions(
            actions,
            recommended_actions=recommended_actions,
        )
        unsupported_actions = sorted(
            set(requested_actions) - RECOVERY_READINESS_APPLY_ACTIONS
        )
        if unsupported_actions:
            raise ValueError(f"Unsupported recovery readiness action: {unsupported_actions[0]}")

        results: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        blocked_reasons = _string_list(restore_readiness.get("blocked_reasons"))
        warnings = _string_list(restore_readiness.get("warnings"))
        metadata_keys = sorted((metadata or {}).keys())
        raw_backup_policy = _backup_settings(workspace.settings)
        raw_restore_drill_policy = _restore_drill_settings(workspace.settings)

        for action in requested_actions:
            if action == "run_archive_export":
                result, skipped_result = self._apply_recovery_archive_export_action(
                    workspace=workspace,
                    user_id=user_id,
                    queue=queue,
                    dry_run=dry_run,
                    reason=reason,
                    metadata_keys=metadata_keys,
                    blocked_reasons=blocked_reasons,
                    warnings=warnings,
                    raw_backup_policy=raw_backup_policy,
                )
            elif action == "run_restore_import_test":
                result, skipped_result = self._apply_recovery_restore_test_action(
                    workspace=workspace,
                    user_id=user_id,
                    storage=storage,
                    dry_run=dry_run,
                    reason=reason,
                    metadata_keys=metadata_keys,
                    blocked_reasons=blocked_reasons,
                    warnings=warnings,
                    raw_restore_drill_policy=raw_restore_drill_policy,
                )
            else:
                continue
            if result is not None:
                results.append(result)
            if skipped_result is not None:
                skipped.append(skipped_result)

        summary = {
            "archive_export_jobs_enqueued": sum(
                1
                for item in results
                if item["action"] == "run_archive_export" and item["status"] == "applied"
            ),
            "restore_import_tests_completed": sum(
                1
                for item in results
                if item["action"] == "run_restore_import_test"
                and item["status"] == "applied"
            ),
            "active_archive_export_job_count": self._active_archive_export_job_count(
                workspace_id
            ),
            "metadata_keys": metadata_keys,
            "readiness_blocked_reasons": blocked_reasons,
            "readiness_warnings": warnings,
            "unsupported_recommended_actions": sorted(
                set(recommended_actions) - RECOVERY_READINESS_APPLY_ACTIONS
            ),
        }
        if not dry_run:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=user_id,
                action="workspace.recovery_readiness.actions_applied",
                target_type="workspace",
                target_id=workspace_id,
                metadata={
                    "requested_actions": requested_actions,
                    "applied_count": len(results),
                    "skipped_count": len(skipped),
                    "summary": summary,
                    "reason": reason,
                },
            )
            self._session.commit()

        return {
            "workspace_id": workspace_id,
            "generated_at": datetime.now(UTC),
            "dry_run": dry_run,
            "status": "dry_run" if dry_run else "applied" if results else "noop",
            "requested_actions": requested_actions,
            "eligible_action_count": len(results),
            "applied_count": 0 if dry_run else len(results),
            "skipped_count": len(skipped),
            "summary": summary,
            "results": results,
            "skipped": skipped,
        }

    def _apply_recovery_archive_export_action(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        queue: RedisQueue,
        dry_run: bool,
        reason: str | None,
        metadata_keys: list[str],
        blocked_reasons: list[str],
        warnings: list[str],
        raw_backup_policy: dict[str, object],
    ) -> tuple[dict[str, object] | None, dict[str, object] | None]:
        action = "run_archive_export"
        if self._has_active_archive_export_job(workspace.id):
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace",
                resource_id=workspace.id,
                reason="archive_export_already_active",
                blocked_reasons=blocked_reasons,
            )
        try:
            request = _scheduled_archive_export_request(raw_backup_policy)
        except ValidationError:
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace",
                resource_id=workspace.id,
                reason="invalid_archive_request",
                blocked_reasons=blocked_reasons,
            )

        request_payload = request.model_dump(mode="json")
        if dry_run:
            return _recovery_action_result(
                action=action,
                resource_type="workspace",
                resource_id=workspace.id,
                status="would_apply",
                blocked_reasons=blocked_reasons,
                metadata={"request": request_payload, "readiness_warnings": warnings},
            ), None

        export_job = WorkspaceExportService(self._session).create_archive_export_job(
            workspace=workspace,
            user_id=user_id,
            request=request,
            queue=queue,
        )
        export_job.job_metadata = {
            **export_job.job_metadata,
            "source": "recovery_readiness_action",
            "reason": reason,
            "metadata_keys": metadata_keys,
            "readiness_blocked_reasons": blocked_reasons,
            "readiness_warnings": warnings,
        }
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=user_id,
            action="workspace.recovery_readiness.archive_export_enqueued",
            target_type="workspace_export_job",
            target_id=export_job.id,
            metadata={
                "reason": reason,
                "metadata_keys": metadata_keys,
                "readiness_blocked_reasons": blocked_reasons,
                "readiness_warnings": warnings,
            },
        )
        return _recovery_action_result(
            action=action,
            resource_type="workspace_export_job",
            resource_id=export_job.id,
            status="applied",
            blocked_reasons=blocked_reasons,
            metadata={"export_job_status": export_job.status, "request": request_payload},
        ), None

    def _apply_recovery_restore_test_action(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        storage: LocalStorage | None,
        dry_run: bool,
        reason: str | None,
        metadata_keys: list[str],
        blocked_reasons: list[str],
        warnings: list[str],
        raw_restore_drill_policy: dict[str, object],
    ) -> tuple[dict[str, object] | None, dict[str, object] | None]:
        action = "run_restore_import_test"
        latest_success = self._latest_successful_archive_export(workspace.id)
        if latest_success is None:
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace",
                resource_id=workspace.id,
                reason="no_successful_archive_export",
                blocked_reasons=blocked_reasons,
            )
        if self._has_active_archive_export_job(workspace.id):
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace_export_job",
                resource_id=latest_success.id,
                reason="archive_export_already_active",
                blocked_reasons=blocked_reasons,
            )
        if storage is None:
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace_export_job",
                resource_id=latest_success.id,
                reason="storage_unavailable",
                blocked_reasons=blocked_reasons,
            )
        try:
            request = _scheduled_restore_drill_request(raw_restore_drill_policy)
        except ValidationError:
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace_export_job",
                resource_id=latest_success.id,
                reason="invalid_restore_drill_request",
                blocked_reasons=blocked_reasons,
            )

        request_payload = request.model_dump(mode="json")
        if dry_run:
            return _recovery_action_result(
                action=action,
                resource_type="workspace_export_job",
                resource_id=latest_success.id,
                status="would_apply",
                blocked_reasons=blocked_reasons,
                metadata={"request": request_payload, "readiness_warnings": warnings},
            ), None

        try:
            drill_result = WorkspaceExportService(self._session).run_archive_restore_drill(
                workspace=workspace,
                user_id=user_id,
                job_id=latest_success.id,
                request=request,
                storage=storage,
            )
        except (FileNotFoundError, ValueError):
            return None, _recovery_action_skipped(
                action=action,
                resource_type="workspace_export_job",
                resource_id=latest_success.id,
                reason="restore_drill_failed",
                blocked_reasons=blocked_reasons,
            )
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=user_id,
            action="workspace.recovery_readiness.restore_import_test_completed",
            target_type="workspace_export_job",
            target_id=latest_success.id,
            metadata={
                "reason": reason,
                "metadata_keys": metadata_keys,
                "readiness_blocked_reasons": blocked_reasons,
                "readiness_warnings": warnings,
                "passed": drill_result["passed"],
                "required_resolution_count": drill_result["required_resolution_count"],
                "suggested_resolution_count": drill_result["suggested_resolution_count"],
                "conflict_counts": drill_result["conflict_counts"],
            },
        )
        return _recovery_action_result(
            action=action,
            resource_type="workspace_export_job",
            resource_id=latest_success.id,
            status="applied",
            blocked_reasons=blocked_reasons,
            metadata={
                "passed": drill_result["passed"],
                "required_resolution_count": drill_result["required_resolution_count"],
                "suggested_resolution_count": drill_result["suggested_resolution_count"],
                "conflict_counts": drill_result["conflict_counts"],
                "request": request_payload,
            },
        ), None

    def preview_retention(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        include_files: bool,
        include_export_jobs: bool,
        include_artifacts: bool,
        max_items: int,
        require_successful_backup: bool,
    ) -> dict[str, object] | None:
        return self._retention_response(
            workspace_id=workspace_id,
            user_id=user_id,
            apply_changes=False,
            include_files=include_files,
            include_export_jobs=include_export_jobs,
            include_artifacts=include_artifacts,
            max_items=max_items,
            require_successful_backup=require_successful_backup,
        )

    def apply_retention(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        include_files: bool,
        include_export_jobs: bool,
        include_artifacts: bool,
        max_items: int,
        require_successful_backup: bool,
    ) -> dict[str, object] | None:
        return self._retention_response(
            workspace_id=workspace_id,
            user_id=user_id,
            apply_changes=True,
            include_files=include_files,
            include_export_jobs=include_export_jobs,
            include_artifacts=include_artifacts,
            max_items=max_items,
            require_successful_backup=require_successful_backup,
        )

    def run_scheduled_lifecycle(
        self,
        *,
        queue: RedisQueue,
        storage: LocalStorage | None = None,
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
        storage: LocalStorage | None,
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

        response = self.apply_retention(
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
        storage: LocalStorage | None,
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
        latest_retention_run_at = self._latest_lifecycle_retention_run_at(workspace.id)
        latest_restore_drill = self._latest_restore_drill_event(workspace.id)
        latest_restore_drill_at = (
            latest_restore_drill.created_at if latest_restore_drill is not None else None
        )
        latest_success = self._latest_successful_archive_export(workspace.id)
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
        active_archive_export_count = self._active_archive_export_job_count(workspace.id)
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
                    self._latest_scheduled_archive_export_job(workspace.id)
                ),
                "latest_event": _audit_event_payload(
                    self._latest_lifecycle_event(
                        workspace.id,
                        _BACKUP_LIFECYCLE_EVENT_ACTIONS,
                    )
                ),
                "recent_events": [
                    _audit_event_payload(event)
                    for event in self._recent_lifecycle_events(
                        workspace.id,
                        _BACKUP_LIFECYCLE_EVENT_ACTIONS,
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
                    self._latest_lifecycle_event(
                        workspace.id,
                        _RETENTION_LIFECYCLE_EVENT_ACTIONS,
                    )
                ),
                "recent_events": [
                    _audit_event_payload(event)
                    for event in self._recent_lifecycle_events(
                        workspace.id,
                        _RETENTION_LIFECYCLE_EVENT_ACTIONS,
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
                    self._latest_lifecycle_event(
                        workspace.id,
                        _RESTORE_DRILL_LIFECYCLE_EVENT_ACTIONS,
                    )
                ),
                "recent_events": [
                    _audit_event_payload(event)
                    for event in self._recent_lifecycle_events(
                        workspace.id,
                        _RESTORE_DRILL_LIFECYCLE_EVENT_ACTIONS,
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

    def _latest_scheduled_archive_export_job(
        self,
        workspace_id: UUID,
    ) -> WorkspaceExportJob | None:
        jobs = self._session.scalars(
            select(WorkspaceExportJob)
            .where(
                WorkspaceExportJob.workspace_id == workspace_id,
                WorkspaceExportJob.export_type == "workspace_archive",
            )
            .order_by(WorkspaceExportJob.created_at.desc(), WorkspaceExportJob.id.desc())
            .limit(20)
        ).all()
        return next(
            (
                job
                for job in jobs
                if job.job_metadata.get("scheduled_by") == "workspace_data_lifecycle"
            ),
            None,
        )

    def _latest_lifecycle_event(
        self,
        workspace_id: UUID,
        actions: tuple[str, ...],
    ) -> AuditEvent | None:
        return self._session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action.in_(actions),
            )
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(1)
        )

    def _recent_lifecycle_events(
        self,
        workspace_id: UUID,
        actions: tuple[str, ...],
        limit: int = 10,
    ) -> list[AuditEvent]:
        return list(
            self._session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.workspace_id == workspace_id,
                    AuditEvent.action.in_(actions),
                )
                .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
                .limit(limit)
            ).all()
        )


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

    def _latest_failed_export_job(self, workspace_id: UUID) -> WorkspaceExportJob | None:
        return self._session.scalar(
            select(WorkspaceExportJob)
            .where(
                WorkspaceExportJob.workspace_id == workspace_id,
                WorkspaceExportJob.status == WorkspaceExportJobStatus.FAILED.value,
            )
            .order_by(WorkspaceExportJob.created_at.desc(), WorkspaceExportJob.id.desc())
            .limit(1)
        )

    def _latest_archive_import_event(self, workspace_id: UUID) -> AuditEvent | None:
        return self._session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action == "workspace.archive_import.created",
            )
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(1)
        )

    def _latest_archive_integrity_event(self, workspace_id: UUID) -> AuditEvent | None:
        return self._session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action == "workspace.archive_export_job.integrity_checked",
            )
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
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

    def _restore_test_history(
        self,
        *,
        workspace_id: UUID,
        latest_success: WorkspaceExportJob | None,
    ) -> dict[str, object]:
        total_tests = int(
            self._session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.workspace_id == workspace_id,
                    AuditEvent.action.in_(_RESTORE_TEST_EVENT_ACTIONS),
                )
            )
            or 0
        )
        events = self._session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action.in_(_RESTORE_TEST_EVENT_ACTIONS),
            )
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(5)
        ).all()
        latest_test = events[0] if events else None
        latest_success_at = (
            latest_success.completed_at
            if latest_success is not None and latest_success.completed_at is not None
            else None
        )
        tests_after_latest_archive = (
            sum(1 for event in events if event.created_at >= latest_success_at)
            if latest_success_at is not None
            else 0
        )
        return {
            "total_tests": total_tests,
            "latest_tested_at": latest_test.created_at if latest_test is not None else None,
            "latest_test_covers_latest_archive": (
                latest_test is not None
                and latest_success_at is not None
                and latest_test.created_at >= latest_success_at
            ),
            "tests_after_latest_archive": tests_after_latest_archive,
            "latest_created_counts": _metadata_counts(latest_test, "created_counts"),
            "latest_skipped_counts": _metadata_counts(latest_test, "skipped_counts"),
            "recent_tests": [_restore_test_payload(event) for event in events],
        }

    def _import_conflict_history(self, workspace_id: UUID) -> dict[str, object]:
        events = self._session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action.in_(
                    [
                        "workspace.import.previewed",
                        "workspace.archive_import.previewed",
                    ]
                ),
            )
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(10)
        ).all()
        latest_preview = events[0] if events else None
        collection_counts: Counter[str] = Counter()
        severity_counts: Counter[str] = Counter()
        strategy_counts: Counter[str] = Counter()
        total_conflicts = 0
        required_resolution_count = 0
        suggested_resolution_count = 0
        previews_with_required_resolution = 0
        for event in events:
            metadata = event.audit_metadata if isinstance(event.audit_metadata, dict) else {}
            event_conflict_counts = _safe_count_map(metadata.get("conflict_counts"))
            collection_counts.update(event_conflict_counts)
            severity_counts.update(_safe_count_map(metadata.get("conflict_severity_counts")))
            strategy_counts.update(_safe_count_map(metadata.get("conflict_strategy_counts")))
            total_conflicts += sum(event_conflict_counts.values())
            event_required_count = _safe_int(metadata.get("required_resolution_count"))
            required_resolution_count += event_required_count
            suggested_resolution_count += _safe_int(metadata.get("suggested_resolution_count"))
            if event_required_count > 0:
                previews_with_required_resolution += 1
        return {
            "total_previews": len(events),
            "total_conflicts": total_conflicts,
            "required_resolution_count": required_resolution_count,
            "suggested_resolution_count": suggested_resolution_count,
            "previews_with_required_resolution": previews_with_required_resolution,
            "latest_previewed_at": (
                latest_preview.created_at if latest_preview is not None else None
            ),
            "latest_preview": _import_preview_payload(latest_preview),
            "conflict_counts": dict(sorted(collection_counts.items())),
            "conflict_severity_counts": dict(sorted(severity_counts.items())),
            "conflict_strategy_counts": dict(sorted(strategy_counts.items())),
            "recent_previews": [_import_preview_payload(event) for event in events],
        }

    def _export_job_stats(self, workspace_id: UUID) -> dict[str, object]:
        jobs = self._session.scalars(
            select(WorkspaceExportJob)
            .where(WorkspaceExportJob.workspace_id == workspace_id)
            .order_by(WorkspaceExportJob.created_at.desc(), WorkspaceExportJob.id.desc())
        ).all()
        status_counts = Counter(job.status for job in jobs)
        type_counts = Counter(job.export_type for job in jobs)
        active_statuses = {
            WorkspaceExportJobStatus.QUEUED.value,
            WorkspaceExportJobStatus.RUNNING.value,
        }
        downloadable_count = sum(
            1
            for job in jobs
            if job.status == WorkspaceExportJobStatus.COMPLETED.value
            and job.storage_key is not None
        )
        return {
            "total": len(jobs),
            "by_status": dict(sorted(status_counts.items())),
            "by_type": dict(sorted(type_counts.items())),
            "active_job_count": sum(1 for job in jobs if job.status in active_statuses),
            "downloadable_archive_count": downloadable_count,
            "failed_job_count": int(status_counts.get(WorkspaceExportJobStatus.FAILED.value, 0)),
            "latest_job": _job_payload(jobs[0] if jobs else None),
        }

    def _archive_coverage_counts(self, workspace_id: UUID) -> dict[str, int]:
        counts: dict[str, int] = {}
        for collection, model in ARCHIVE_COVERAGE_MODELS.items():
            counts[collection] = int(
                self._session.scalar(
                    select(func.count(model.id)).where(model.workspace_id == workspace_id)
                )
                or 0
            )
        return counts

    def _file_stats(self, workspace_id: UUID) -> dict[str, object]:
        count, total_bytes = self._session.execute(
            select(
                func.count(WorkspaceFile.id),
                func.coalesce(func.sum(WorkspaceFile.size_bytes), 0),
            ).where(WorkspaceFile.workspace_id == workspace_id)
        ).one()
        active_count = self._session.scalar(
            select(func.count(WorkspaceFile.id)).where(
                WorkspaceFile.workspace_id == workspace_id,
                WorkspaceFile.status == "active",
            )
        )
        latest_upload = self._session.scalar(
            select(func.max(WorkspaceFile.created_at)).where(
                WorkspaceFile.workspace_id == workspace_id
            )
        )
        return {
            "total_count": int(count or 0),
            "active_count": int(active_count or 0),
            "total_bytes": int(total_bytes or 0),
            "latest_uploaded_at": latest_upload,
        }

    def _artifact_stats(self, workspace_id: UUID) -> dict[str, object]:
        count, total_bytes = self._session.execute(
            select(
                func.count(Artifact.id),
                func.coalesce(func.sum(Artifact.size_bytes), 0),
            ).where(Artifact.workspace_id == workspace_id)
        ).one()
        versioned_count = self._session.scalar(
            select(func.count(Artifact.id)).where(
                Artifact.workspace_id == workspace_id,
                Artifact.version > 1,
            )
        )
        superseded_count = self._session.scalar(
            select(func.count(Artifact.id)).where(
                Artifact.workspace_id == workspace_id,
                Artifact.supersedes_artifact_id.is_not(None),
            )
        )
        latest_artifact = self._session.scalar(
            select(func.max(Artifact.created_at)).where(Artifact.workspace_id == workspace_id)
        )
        return {
            "total_count": int(count or 0),
            "total_bytes": int(total_bytes or 0),
            "versioned_count": int(versioned_count or 0),
            "superseded_count": int(superseded_count or 0),
            "latest_created_at": latest_artifact,
        }

    def _access_stats(self, workspace_id: UUID) -> dict[str, object]:
        events = self._session.scalars(
            select(FileAccessEvent).where(FileAccessEvent.workspace_id == workspace_id)
        ).all()
        action_counts = Counter(event.action for event in events)
        latest_access = max((event.created_at for event in events), default=None)
        return {
            "total_events": len(events),
            "by_action": dict(sorted(action_counts.items())),
            "latest_access_at": latest_access,
            "download_audit_enabled": True,
        }

    def _retention_response(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        apply_changes: bool,
        include_files: bool,
        include_export_jobs: bool,
        include_artifacts: bool,
        max_items: int,
        require_successful_backup: bool,
    ) -> dict[str, object] | None:
        workspace = self._session.get(Workspace, workspace_id)
        if workspace is None:
            return None

        generated_at = datetime.now(UTC)
        policy = _retention_policy(workspace.settings)
        latest_success = self._latest_successful_archive_export(workspace_id)
        blocked_reasons = _retention_blocked_reasons(
            policy=policy,
            require_successful_backup=require_successful_backup,
            latest_success=latest_success,
            include_files=include_files,
            include_export_jobs=include_export_jobs,
            include_artifacts=include_artifacts,
        )
        candidates: list[dict[str, object]] = []
        if not blocked_reasons:
            candidates = self._retention_candidates(
                workspace_id=workspace_id,
                generated_at=generated_at,
                policy=policy,
                include_files=include_files,
                include_export_jobs=include_export_jobs,
                include_artifacts=include_artifacts,
                max_items=max_items,
            )

        applied_counts = {"files": 0, "export_jobs": 0, "artifacts": 0}
        if apply_changes and not blocked_reasons:
            applied_counts = self._apply_file_retention(candidates)

        counts = _candidate_counts(candidates)
        warnings = _retention_warnings(policy, candidates)
        recommended_actions = _retention_recommended_actions(
            blocked_reasons=blocked_reasons,
            warnings=warnings,
            candidates=candidates,
            apply_changes=apply_changes,
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action=(
                "workspace.retention_applied"
                if apply_changes and not blocked_reasons
                else "workspace.retention_previewed"
            ),
            target_type="workspace",
            target_id=workspace_id,
            metadata={
                "dry_run": not apply_changes,
                "blocked_reasons": blocked_reasons,
                "candidate_counts": counts,
                "applied_counts": applied_counts,
                "recommended_actions": recommended_actions,
                "include_files": include_files,
                "include_export_jobs": include_export_jobs,
                "include_artifacts": include_artifacts,
                "require_successful_backup": require_successful_backup,
            },
        )
        self._session.commit()
        return {
            "workspace_id": workspace_id,
            "generated_at": generated_at,
            "dry_run": not apply_changes,
            "applied": apply_changes and not blocked_reasons,
            "policy": policy,
            "blocked_reasons": blocked_reasons,
            "warnings": warnings,
            "recommended_actions": recommended_actions,
            "counts": counts,
            "applied_counts": applied_counts,
            "candidates": candidates,
        }

    def _retention_candidates(
        self,
        *,
        workspace_id: UUID,
        generated_at: datetime,
        policy: dict[str, object],
        include_files: bool,
        include_export_jobs: bool,
        include_artifacts: bool,
        max_items: int,
    ) -> list[dict[str, object]]:
        candidates: list[dict[str, object]] = []
        if include_files:
            candidates.extend(
                self._file_retention_candidates(
                    workspace_id=workspace_id,
                    generated_at=generated_at,
                    retention_days=_retention_days(policy, "file_retention_days"),
                    delete_policy=str(policy.get("delete_policy") or "manual_review"),
                    limit=max_items - len(candidates),
                )
            )
        if include_export_jobs and len(candidates) < max_items:
            candidates.extend(
                self._export_job_retention_candidates(
                    workspace_id=workspace_id,
                    generated_at=generated_at,
                    retention_days=_retention_days(policy, "export_job_retention_days"),
                    limit=max_items - len(candidates),
                )
            )
        if include_artifacts and len(candidates) < max_items:
            candidates.extend(
                self._artifact_retention_candidates(
                    workspace_id=workspace_id,
                    generated_at=generated_at,
                    retention_days=_retention_days(policy, "artifact_retention_days"),
                    limit=max_items - len(candidates),
                )
            )
        return candidates

    def _file_retention_candidates(
        self,
        *,
        workspace_id: UUID,
        generated_at: datetime,
        retention_days: int | None,
        delete_policy: str,
        limit: int,
    ) -> list[dict[str, object]]:
        if retention_days is None or limit <= 0:
            return []
        action = "soft_delete" if delete_policy == "soft_delete" else "manual_review"
        reason = (
            None
            if action == "soft_delete"
            else "retention_delete_policy_requires_manual_review"
        )
        cutoff = generated_at - timedelta(days=retention_days)
        files = self._session.scalars(
            select(WorkspaceFile)
            .where(
                WorkspaceFile.workspace_id == workspace_id,
                WorkspaceFile.status == "active",
                WorkspaceFile.created_at < cutoff,
            )
            .order_by(WorkspaceFile.created_at.asc(), WorkspaceFile.id.asc())
            .limit(limit)
        ).all()
        return [
            _candidate_payload(
                resource_type="file",
                resource_id=file.id,
                created_at=file.created_at,
                generated_at=generated_at,
                retention_days=retention_days,
                status=file.status,
                action=action,
                filename=file.filename,
                size_bytes=file.size_bytes,
                reason=reason,
            )
            for file in files
        ]

    def _export_job_retention_candidates(
        self,
        *,
        workspace_id: UUID,
        generated_at: datetime,
        retention_days: int | None,
        limit: int,
    ) -> list[dict[str, object]]:
        if retention_days is None or limit <= 0:
            return []
        cutoff = generated_at - timedelta(days=retention_days)
        jobs = self._session.scalars(
            select(WorkspaceExportJob)
            .where(
                WorkspaceExportJob.workspace_id == workspace_id,
                WorkspaceExportJob.status.in_(
                    [
                        WorkspaceExportJobStatus.COMPLETED.value,
                        WorkspaceExportJobStatus.FAILED.value,
                    ]
                ),
                WorkspaceExportJob.created_at < cutoff,
            )
            .order_by(WorkspaceExportJob.created_at.asc(), WorkspaceExportJob.id.asc())
            .limit(limit)
        ).all()
        return [
            _candidate_payload(
                resource_type="export_job",
                resource_id=job.id,
                created_at=job.created_at,
                generated_at=generated_at,
                retention_days=retention_days,
                status=job.status,
                action="manual_review",
                filename=job.filename,
                size_bytes=job.size_bytes,
                reason="export_job_storage_requires_operator_review",
            )
            for job in jobs
        ]

    def _artifact_retention_candidates(
        self,
        *,
        workspace_id: UUID,
        generated_at: datetime,
        retention_days: int | None,
        limit: int,
    ) -> list[dict[str, object]]:
        if retention_days is None or limit <= 0:
            return []
        cutoff = generated_at - timedelta(days=retention_days)
        artifacts = self._session.scalars(
            select(Artifact)
            .where(
                Artifact.workspace_id == workspace_id,
                Artifact.created_at < cutoff,
            )
            .order_by(Artifact.created_at.asc(), Artifact.id.asc())
            .limit(limit)
        ).all()
        return [
            _candidate_payload(
                resource_type="artifact",
                resource_id=artifact.id,
                created_at=artifact.created_at,
                generated_at=generated_at,
                retention_days=retention_days,
                status=artifact.review_status,
                action="manual_review",
                filename=artifact.filename,
                size_bytes=artifact.size_bytes,
                reason="artifact_retention_requires_operator_review",
            )
            for artifact in artifacts
        ]

    def _apply_file_retention(self, candidates: list[dict[str, object]]) -> dict[str, int]:
        applied_counts = {"files": 0, "export_jobs": 0, "artifacts": 0}
        file_ids = [
            candidate["resource_id"]
            for candidate in candidates
            if candidate["resource_type"] == "file" and candidate["action"] == "soft_delete"
        ]
        if not file_ids:
            return applied_counts

        files = self._session.scalars(
            select(WorkspaceFile).where(
                WorkspaceFile.id.in_(file_ids),
                WorkspaceFile.status == "active",
            )
        ).all()
        now = datetime.now(UTC)
        for file in files:
            file.status = RETENTION_DELETED_FILE_STATUS
            file.file_metadata = {
                **file.file_metadata,
                "retention_deleted_at": now.isoformat(),
                "retention_delete_policy": "soft_delete",
            }
            applied_counts["files"] += 1
        return applied_counts


def _lifecycle_settings(settings: dict[str, object]) -> dict[str, object]:
    data_lifecycle = settings.get("data_lifecycle") if isinstance(settings, dict) else None
    return data_lifecycle if isinstance(data_lifecycle, dict) else {}


def _backup_settings(settings: dict[str, object]) -> dict[str, object]:
    lifecycle = _lifecycle_settings(settings)
    raw_policy = lifecycle.get("backup")
    return raw_policy if isinstance(raw_policy, dict) else {}


def _retention_settings(settings: dict[str, object]) -> dict[str, object]:
    lifecycle = _lifecycle_settings(settings)
    raw_policy = lifecycle.get("retention")
    return raw_policy if isinstance(raw_policy, dict) else {}


def _restore_drill_settings(settings: dict[str, object]) -> dict[str, object]:
    lifecycle = _lifecycle_settings(settings)
    raw_policy = lifecycle.get("restore_drill")
    return raw_policy if isinstance(raw_policy, dict) else {}


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
    return bool(
        isinstance(schedule_status, dict)
        and schedule_status.get("configured") is True
    )


def _automation_backup_warnings(
    backup_policy: dict[str, object],
    *,
    active_archive_export_count: int,
) -> list[str]:
    warnings = (
        list(backup_policy["warnings"])
        if isinstance(backup_policy["warnings"], list)
        else []
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
        list(retention_policy["warnings"])
        if isinstance(retention_policy["warnings"], list)
        else []
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


def _retention_policy(settings: dict[str, object]) -> dict[str, object]:
    raw_policy = _retention_settings(settings)
    enabled = bool(raw_policy.get("enabled", False))
    warnings: list[str] = []
    if not enabled:
        warnings.append("retention_policy_not_enabled")
    return {
        "enabled": enabled,
        "source": "workspace.settings.data_lifecycle.retention",
        "default_retention_days": _positive_int(raw_policy.get("default_retention_days")),
        "file_retention_days": _positive_int(raw_policy.get("file_retention_days")),
        "export_job_retention_days": _positive_int(raw_policy.get("export_job_retention_days")),
        "artifact_retention_days": _positive_int(raw_policy.get("artifact_retention_days")),
        "audit_event_retention_days": _positive_int(raw_policy.get("audit_event_retention_days")),
        "delete_policy": str(raw_policy.get("delete_policy") or "manual_review"),
        "warnings": warnings,
    }


def _backup_policy(
    settings: dict[str, object],
    latest_job: WorkspaceExportJob | None,
    latest_success: WorkspaceExportJob | None,
    *,
    generated_at: datetime,
) -> dict[str, object]:
    raw_policy = _backup_settings(settings)
    enabled = bool(raw_policy.get("enabled", False))
    warnings: list[str] = []
    if not enabled:
        warnings.append("backup_policy_not_enabled")
    if latest_success is None:
        warnings.append("no_successful_archive_export")
    schedule_status = _backup_schedule_status(
        raw_policy=raw_policy,
        enabled=enabled,
        latest_success=latest_success,
        generated_at=generated_at,
    )
    warnings.extend(schedule_status["warnings"])
    return {
        "enabled": enabled,
        "source": "workspace.settings.data_lifecycle.backup",
        "schedule": raw_policy.get("schedule"),
        "target_type": raw_policy.get("target_type") or "manual_export",
        "target": raw_policy.get("target"),
        "max_archive_age_days": _positive_int(
            raw_policy.get("max_archive_age_days") or raw_policy.get("max_age_days")
        ),
        "last_export_job_status": latest_job.status if latest_job is not None else None,
        "last_successful_archive_export_at": (
            latest_success.completed_at if latest_success is not None else None
        ),
        "schedule_status": schedule_status,
        "warnings": warnings,
    }


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
    explicit_interval = _positive_int(
        raw_policy.get("interval_hours") or raw_policy.get("schedule_interval_hours")
    )
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


def _readiness(
    retention_policy: dict[str, object],
    backup_policy: dict[str, object],
    latest_success: WorkspaceExportJob | None,
) -> dict[str, object]:
    blocked_reasons: list[str] = []
    if retention_policy["enabled"] is not True:
        blocked_reasons.append("retention_policy_not_enabled")
    if backup_policy["enabled"] is not True:
        blocked_reasons.append("backup_policy_not_enabled")
    if latest_success is None:
        blocked_reasons.append("no_successful_archive_export")
    return {
        "ready": not blocked_reasons,
        "blocked_reasons": blocked_reasons,
        "warnings": [*retention_policy["warnings"], *backup_policy["warnings"]],
    }


def _restore_readiness(
    *,
    latest_success: WorkspaceExportJob | None,
    backup_policy: dict[str, object],
    generated_at: datetime,
    active_job_count: object,
    backup_coverage: dict[str, object],
    restore_test_history: dict[str, object],
    import_conflict_history: dict[str, object],
) -> dict[str, object]:
    blocked_reasons: list[str] = []
    warnings: list[str] = []
    latest_archive_age_days = (
        _age_days(generated_at, latest_success.completed_at)
        if latest_success is not None and latest_success.completed_at is not None
        else None
    )
    max_archive_age_days = backup_policy.get("max_archive_age_days")
    if backup_policy["enabled"] is not True:
        blocked_reasons.append("backup_policy_not_enabled")
    if latest_success is None:
        blocked_reasons.append("no_successful_archive_export")
    else:
        if latest_success.storage_key is None:
            blocked_reasons.append("latest_archive_missing_storage_object")
        if latest_success.checksum_sha256 is None:
            blocked_reasons.append("latest_archive_missing_checksum")
        if latest_success.size_bytes is None or latest_success.size_bytes <= 0:
            blocked_reasons.append("latest_archive_empty_or_unknown_size")
        if (
            isinstance(max_archive_age_days, int)
            and latest_archive_age_days is not None
            and latest_archive_age_days > max_archive_age_days
        ):
            blocked_reasons.append("latest_archive_stale")
    latest_tested_at = restore_test_history.get("latest_tested_at")
    if latest_tested_at is None:
        blocked_reasons.append("no_archive_import_test_recorded")
    elif (
        latest_success is not None
        and latest_success.completed_at is not None
        and latest_tested_at < latest_success.completed_at
    ):
        blocked_reasons.append("restore_test_older_than_latest_archive")
    if backup_coverage.get("status") == "partial":
        blocked_reasons.append("backup_coverage_incomplete")
    if isinstance(active_job_count, int) and active_job_count > 0:
        warnings.append("archive_export_jobs_in_progress")
    if backup_coverage.get("status") == "unknown":
        warnings.append("backup_coverage_unknown")
    if _safe_int(import_conflict_history.get("required_resolution_count")) > 0:
        warnings.append("import_previews_have_required_resolutions")
    if "backup_schedule_overdue" in backup_policy.get("warnings", []):
        warnings.append("backup_schedule_overdue")
    return {
        "ready": not blocked_reasons,
        "blocked_reasons": blocked_reasons,
        "warnings": warnings,
        "backup_coverage": backup_coverage,
        "restore_test_history": restore_test_history,
        "import_conflict_history": import_conflict_history,
        "downloadable_archive_available": (
            latest_success is not None
            and latest_success.status == WorkspaceExportJobStatus.COMPLETED.value
            and latest_success.storage_key is not None
        ),
        "latest_archive_age_days": latest_archive_age_days,
        "max_archive_age_days": max_archive_age_days,
        "latest_archive_import_test_recorded": latest_tested_at is not None,
        "latest_archive_import_tested_at": latest_tested_at,
        "recommended_actions": _restore_recommended_actions(
            blocked_reasons,
            warnings=warnings,
        ),
    }


def _backup_coverage(
    *,
    latest_success: WorkspaceExportJob | None,
    current_counts: dict[str, int],
) -> dict[str, object]:
    if latest_success is None:
        return {
            "status": "missing",
            "score": 0,
            "current_counts": current_counts,
            "archived_counts": {},
            "uncovered_counts": current_counts,
            "total_current_resources": sum(current_counts.values()),
            "total_archived_resources": 0,
            "uncovered_resource_count": sum(current_counts.values()),
        }

    archived_counts = _manifest_counts(latest_success.job_metadata)
    if not archived_counts:
        return {
            "status": "unknown",
            "score": 50,
            "current_counts": current_counts,
            "archived_counts": {},
            "uncovered_counts": {},
            "total_current_resources": sum(current_counts.values()),
            "total_archived_resources": 0,
            "uncovered_resource_count": None,
        }

    uncovered_counts = {
        collection: max(current_count - int(archived_counts.get(collection, 0)), 0)
        for collection, current_count in current_counts.items()
    }
    total_current = sum(current_counts.values())
    total_uncovered = sum(uncovered_counts.values())
    score = (
        100
        if total_current == 0
        else int(((total_current - total_uncovered) / total_current) * 100)
    )
    return {
        "status": "verified" if total_uncovered == 0 else "partial",
        "score": max(min(score, 100), 0),
        "current_counts": current_counts,
        "archived_counts": {
            collection: int(archived_counts.get(collection, 0))
            for collection in current_counts
        },
        "uncovered_counts": {
            collection: count for collection, count in uncovered_counts.items() if count > 0
        },
        "total_current_resources": total_current,
        "total_archived_resources": sum(
            int(archived_counts.get(collection, 0)) for collection in current_counts
        ),
        "uncovered_resource_count": total_uncovered,
    }


def _manifest_counts(metadata: dict[str, object]) -> dict[str, int]:
    for key in ("manifest_counts", "counts"):
        raw_counts = metadata.get(key)
        if isinstance(raw_counts, dict):
            return {
                str(collection): int(count)
                for collection, count in raw_counts.items()
                if isinstance(count, int) and count >= 0
            }
    return {}


def _restore_recommended_actions(
    blocked_reasons: list[str],
    *,
    warnings: list[str] | None = None,
) -> list[str]:
    actions: list[str] = []
    warning_set = set(warnings or [])
    if "backup_policy_not_enabled" in blocked_reasons:
        actions.append("enable_backup_policy")
    if (
        "no_successful_archive_export" in blocked_reasons
        or "latest_archive_stale" in blocked_reasons
        or "backup_coverage_incomplete" in blocked_reasons
    ):
        actions.append("run_archive_export")
    if "backup_coverage_unknown" in warning_set:
        actions.append("run_archive_export_with_manifest_counts")
    if "backup_schedule_overdue" in warning_set and "run_archive_export" not in actions:
        actions.append("run_archive_export")
    if any(
        reason in blocked_reasons
        for reason in {
            "latest_archive_missing_storage_object",
            "latest_archive_missing_checksum",
            "latest_archive_empty_or_unknown_size",
        }
    ):
        actions.append("repair_or_regenerate_archive_export")
    if "no_archive_import_test_recorded" in blocked_reasons:
        actions.append("run_restore_import_test")
    if "restore_test_older_than_latest_archive" in blocked_reasons:
        actions.append("run_restore_import_test")
    if "import_previews_have_required_resolutions" in warning_set:
        actions.append("resolve_import_conflicts_before_restore")
    return actions


def _recovery_readiness_actions(
    actions: list[str] | None,
    *,
    recommended_actions: list[str],
) -> list[str]:
    if not actions:
        if "run_archive_export" in recommended_actions:
            return ["run_archive_export"]
        return _unique_strings(
            action
            for action in recommended_actions
            if action in RECOVERY_READINESS_APPLY_ACTIONS
        )
    return _unique_strings(actions)


def _recovery_action_result(
    *,
    action: str,
    resource_type: str,
    resource_id: UUID,
    status: str,
    blocked_reasons: list[str],
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "status": status,
        "blocked_reasons": blocked_reasons,
        "metadata": metadata or {},
    }


def _recovery_action_skipped(
    *,
    action: str,
    resource_type: str,
    resource_id: UUID,
    reason: str,
    blocked_reasons: list[str],
) -> dict[str, object]:
    return {
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "status": "skipped",
        "reason": reason,
        "blocked_reasons": blocked_reasons,
    }


def _unique_strings(values: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        item = value.strip()
        if not item or item in seen:
            continue
        normalized.append(item)
        seen.add(item)
    return normalized


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


def _safe_count_map(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(collection): int(count)
        for collection, count in value.items()
        if isinstance(count, int) and count >= 0
    }


def _safe_int(value: object) -> int:
    return int(value) if isinstance(value, int) and value >= 0 else 0


def _safe_conflict_summaries(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    summaries: list[dict[str, object]] = []
    for item in value[:10]:
        if not isinstance(item, dict):
            continue
        summaries.append(
            {
                "collection": str(item.get("collection") or ""),
                "field": item.get("field") if isinstance(item.get("field"), str) else None,
                "strategy": str(item.get("strategy") or ""),
                "severity": str(item.get("severity") or ""),
            }
        )
    return summaries


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


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


def _positive_int(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    return None


def _bool_setting(settings: dict[str, object], key: str, default: bool) -> bool:
    value = settings.get(key)
    return value if isinstance(value, bool) else default


def _retention_days(policy: dict[str, object], key: str) -> int | None:
    value = policy.get(key) or policy.get("default_retention_days")
    return value if isinstance(value, int) and value > 0 else None


def _retention_blocked_reasons(
    *,
    policy: dict[str, object],
    require_successful_backup: bool,
    latest_success: WorkspaceExportJob | None,
    include_files: bool,
    include_export_jobs: bool,
    include_artifacts: bool,
) -> list[str]:
    reasons: list[str] = []
    if policy["enabled"] is not True:
        reasons.append("retention_policy_not_enabled")
    if require_successful_backup and latest_success is None:
        reasons.append("no_successful_archive_export")
    if not any([include_files, include_export_jobs, include_artifacts]):
        reasons.append("no_retention_targets_enabled")
    return reasons


def _retention_warnings(
    policy: dict[str, object],
    candidates: list[dict[str, object]],
) -> list[str]:
    warnings = list(policy["warnings"]) if isinstance(policy.get("warnings"), list) else []
    if any(candidate["action"] == "manual_review" for candidate in candidates):
        warnings.append("manual_review_candidates_present")
    return warnings


def _retention_recommended_actions(
    *,
    blocked_reasons: list[str],
    warnings: list[str],
    candidates: list[dict[str, object]],
    apply_changes: bool,
) -> list[str]:
    actions: list[str] = []
    reason_set = set(blocked_reasons)
    warning_set = set(warnings)
    if "retention_policy_not_enabled" in reason_set:
        actions.append("enable_retention_policy")
    if "no_successful_archive_export" in reason_set:
        actions.append("run_archive_export")
    if "no_retention_targets_enabled" in reason_set:
        actions.append("select_retention_targets")
    if "manual_review_candidates_present" in warning_set:
        actions.append("review_retention_candidates")
    if not blocked_reasons and candidates and not apply_changes:
        actions.append("apply_retention")
    if not blocked_reasons and not candidates:
        actions.append("no_retention_candidates")
    return actions


def _candidate_payload(
    *,
    resource_type: str,
    resource_id: UUID,
    created_at: datetime,
    generated_at: datetime,
    retention_days: int,
    status: str,
    action: str,
    filename: str | None,
    size_bytes: int | None,
    reason: str | None = None,
) -> dict[str, object]:
    return {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "created_at": created_at,
        "age_days": _age_days(generated_at, created_at),
        "retention_days": retention_days,
        "status": status,
        "action": action,
        "filename": filename,
        "size_bytes": size_bytes,
        "reason": reason,
    }


def _age_days(now: datetime, then: datetime) -> int:
    normalized_then = _ensure_utc_datetime(then) or then.replace(tzinfo=UTC)
    return max((now - normalized_then).days, 0)


def _ensure_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _candidate_counts(candidates: list[dict[str, object]]) -> dict[str, int]:
    counts = {"files": 0, "export_jobs": 0, "artifacts": 0, "total": len(candidates)}
    for candidate in candidates:
        if candidate["resource_type"] == "file":
            counts["files"] += 1
        elif candidate["resource_type"] == "export_job":
            counts["export_jobs"] += 1
        elif candidate["resource_type"] == "artifact":
            counts["artifacts"] += 1
    return counts
