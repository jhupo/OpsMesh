from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import TypedDict
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.domains.agents.models import AgentProfile
from backend.app.domains.capabilities.models import WorkspaceSkillInstall
from backend.app.domains.orchestration.runs.models import AgentRun, RunEvent
from backend.app.domains.orchestration.tasks.models import Task, TaskMessage, TaskStep
from backend.app.domains.workspace.data_lifecycle.policy import (
    _backup_policy,
    _readiness,
    _retention_policy,
)
from backend.app.domains.workspace.data_lifecycle.recovery import (
    _backup_coverage,
    _restore_readiness,
)
from backend.app.domains.workspace.data_lifecycle.repository import WorkspaceDataLifecycleRepository
from backend.app.domains.workspace.data_lifecycle.scheduling import (
    _automation_backup_warnings,
    _automation_restore_drill_warnings,
    _automation_retention_warnings,
    _backup_interval_hours,
    _restore_drill_due,
    _schedule_configured,
    _scheduled_backup_due,
)
from backend.app.domains.workspace.data_lifecycle.settings import (
    _restore_drill_settings,
    _retention_settings,
    _safe_conflict_summaries,
    _safe_count_map,
    _safe_int,
    _string_list,
)
from backend.app.domains.workspace.data_transfer.contracts import SUPPORTED_WORKSPACE_EXPORT_FORMAT
from backend.app.domains.workspace.data_transfer.models import (
    WorkspaceExportJob,
    WorkspaceExportJobStatus,
)
from backend.app.domains.workspace.storage.artifact_models import Artifact
from backend.app.domains.workspace.storage.models import FileAccessEvent, WorkspaceFile
from backend.app.domains.workspace.teams.models import AgentTeam, AgentTeamMember
from backend.app.domains.workspace.tenants.models import Workspace
from backend.app.observability.audit.models import AuditEvent
from backend.app.runtime.environment.spaces.models import RuntimeSpace, RuntimeSpaceQuota


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

ARCHIVE_COVERAGE_COLUMNS = {
    "agents": (AgentProfile.id, AgentProfile.workspace_id),
    "teams": (AgentTeam.id, AgentTeam.workspace_id),
    "team_members": (AgentTeamMember.id, AgentTeamMember.workspace_id),
    "tasks": (Task.id, Task.workspace_id),
    "task_steps": (TaskStep.id, TaskStep.workspace_id),
    "task_messages": (TaskMessage.id, TaskMessage.workspace_id),
    "runs": (AgentRun.id, AgentRun.workspace_id),
    "run_events": (RunEvent.id, RunEvent.workspace_id),
    "files": (WorkspaceFile.id, WorkspaceFile.workspace_id),
    "artifacts": (Artifact.id, Artifact.workspace_id),
    "runtime_spaces": (RuntimeSpace.id, RuntimeSpace.workspace_id),
    "runtime_space_quotas": (RuntimeSpaceQuota.id, RuntimeSpaceQuota.workspace_id),
    "skill_installs": (WorkspaceSkillInstall.id, WorkspaceSkillInstall.workspace_id),
}
RESTORE_TEST_EVENT_ACTIONS = (
    "workspace.archive_import.created",
    "workspace.archive_restore_drill.completed",
)


class FileStats(TypedDict):
    total_count: int
    active_count: int
    total_bytes: int
    latest_uploaded_at: datetime | None


class ArtifactStats(TypedDict):
    total_count: int
    total_bytes: int
    versioned_count: int
    superseded_count: int
    latest_created_at: datetime | None


class WorkspaceDataLifecycleDiagnosticQueries:
    def __init__(self, session: Session) -> None:
        self._session = session

    def restore_test_history(
        self,
        *,
        workspace_id: UUID,
        latest_success: WorkspaceExportJob | None,
    ) -> dict[str, object]:
        total_tests = int(
            self._session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.workspace_id == workspace_id,
                    AuditEvent.action.in_(RESTORE_TEST_EVENT_ACTIONS),
                )
            )
            or 0
        )
        events = self._session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action.in_(RESTORE_TEST_EVENT_ACTIONS),
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

    def import_conflict_history(self, workspace_id: UUID) -> dict[str, object]:
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

    def export_job_stats(self, workspace_id: UUID) -> dict[str, object]:
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

    def archive_coverage_counts(self, workspace_id: UUID) -> dict[str, int]:
        counts: dict[str, int] = {}
        for collection, (id_column, workspace_column) in ARCHIVE_COVERAGE_COLUMNS.items():
            counts[collection] = int(
                self._session.scalar(
                    select(func.count(id_column)).where(workspace_column == workspace_id)
                )
                or 0
            )
        return counts

    def file_stats(self, workspace_id: UUID) -> FileStats:
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

    def artifact_stats(self, workspace_id: UUID) -> ArtifactStats:
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

    def access_stats(self, workspace_id: UUID) -> dict[str, object]:
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
        self._diagnostic_queries = WorkspaceDataLifecycleDiagnosticQueries(session)

    def get_diagnostics(self, *, workspace_id: UUID) -> dict[str, object] | None:
        workspace = self._session.get(Workspace, workspace_id)
        if workspace is None:
            return None

        generated_at = datetime.now(UTC)
        latest_job = self._repo.latest_export_job(workspace_id)
        latest_success = self._repo.latest_successful_archive_export(workspace_id)
        file_stats = self._diagnostic_queries.file_stats(workspace_id)
        artifact_stats = self._diagnostic_queries.artifact_stats(workspace_id)
        access_stats = self._diagnostic_queries.access_stats(workspace_id)
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
        latest_job = self._repo.latest_export_job(workspace_id)
        latest_success = self._repo.latest_successful_archive_export(workspace_id)
        latest_import = self._repo.latest_archive_import_event(workspace_id)
        latest_restore_drill = self._repo.latest_restore_drill_event(workspace_id)
        latest_integrity = self._repo.latest_archive_integrity_event(workspace_id)
        latest_failed_job = self._repo.latest_failed_export_job(workspace_id)
        job_stats = self._diagnostic_queries.export_job_stats(workspace_id)
        current_counts = self._diagnostic_queries.archive_coverage_counts(workspace_id)
        restore_test_history = self._diagnostic_queries.restore_test_history(
            workspace_id=workspace_id,
            latest_success=latest_success,
        )
        import_conflict_history = self._diagnostic_queries.import_conflict_history(workspace_id)
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
                    *_string_list(retention_policy["warnings"]),
                    *_string_list(backup_policy["warnings"]),
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
        latest_retention_run_at = self._repo.latest_lifecycle_retention_run_at(workspace.id)
        latest_restore_drill = self._repo.latest_restore_drill_event(workspace.id)
        latest_restore_drill_at = (
            latest_restore_drill.created_at if latest_restore_drill is not None else None
        )
        latest_success = self._repo.latest_successful_archive_export(workspace.id)
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
        active_archive_export_count = self._repo.active_archive_export_job_count(workspace.id)
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
                    self._repo.latest_scheduled_archive_export_job(workspace.id)
                ),
                "latest_event": _audit_event_payload(
                    self._repo.latest_lifecycle_event(
                        workspace.id,
                        BACKUP_LIFECYCLE_EVENT_ACTIONS,
                    )
                ),
                "recent_events": [
                    _audit_event_payload(event)
                    for event in self._repo.recent_lifecycle_events(
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
                    self._repo.latest_lifecycle_event(
                        workspace.id,
                        RETENTION_LIFECYCLE_EVENT_ACTIONS,
                    )
                ),
                "recent_events": [
                    _audit_event_payload(event)
                    for event in self._repo.recent_lifecycle_events(
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
                    self._repo.latest_lifecycle_event(
                        workspace.id,
                        RESTORE_DRILL_LIFECYCLE_EVENT_ACTIONS,
                    )
                ),
                "recent_events": [
                    _audit_event_payload(event)
                    for event in self._repo.recent_lifecycle_events(
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
