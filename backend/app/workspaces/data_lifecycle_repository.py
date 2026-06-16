from collections import Counter
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.artifacts.models import Artifact
from backend.app.audit.models import AuditEvent
from backend.app.audit.service import AuditService
from backend.app.capabilities.models import WorkspaceSkillInstall
from backend.app.exports.models import WorkspaceExportJob
from backend.app.exports.status import WorkspaceExportJobStatus
from backend.app.files.models import FileAccessEvent, WorkspaceFile
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceQuota
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workspaces.data_lifecycle_payloads import (
    _import_preview_payload,
    _job_payload,
    _metadata_counts,
    _restore_test_payload,
)
from backend.app.workspaces.data_lifecycle_settings import (
    _ensure_utc_datetime,
    _safe_count_map,
    _safe_int,
)
from backend.app.workspaces.models import Workspace

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
RESTORE_TEST_EVENT_ACTIONS = (
    "workspace.archive_import.created",
    "workspace.archive_restore_drill.completed",
)


class WorkspaceDataLifecycleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def has_active_archive_export_job(self, workspace_id: UUID) -> bool:
        return self.active_archive_export_job_count(workspace_id) > 0

    def active_archive_export_job_count(self, workspace_id: UUID) -> int:
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

    def latest_scheduled_archive_export_job(
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

    def latest_lifecycle_event(
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

    def recent_lifecycle_events(
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


    def latest_lifecycle_retention_run_at(self, workspace_id: UUID) -> datetime | None:
        latest = self._session.scalar(
            select(func.max(AuditEvent.created_at)).where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action == "workspace.retention_applied",
            )
        )
        return _ensure_utc_datetime(latest)

    def record_lifecycle_schedule_event(
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

    def latest_export_job(self, workspace_id: UUID) -> WorkspaceExportJob | None:
        return self._session.scalar(
            select(WorkspaceExportJob)
            .where(WorkspaceExportJob.workspace_id == workspace_id)
            .order_by(WorkspaceExportJob.created_at.desc(), WorkspaceExportJob.id.desc())
            .limit(1)
        )

    def latest_successful_archive_export(self, workspace_id: UUID) -> WorkspaceExportJob | None:
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

    def retention_export_job_candidates(
        self,
        *,
        workspace_id: UUID,
        cutoff: datetime,
        limit: int,
    ) -> list[WorkspaceExportJob]:
        if limit <= 0:
            return []
        return list(
            self._session.scalars(
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
                .order_by(
                    WorkspaceExportJob.created_at.asc(),
                    WorkspaceExportJob.id.asc(),
                )
                .limit(limit)
            ).all()
        )

    def latest_failed_export_job(self, workspace_id: UUID) -> WorkspaceExportJob | None:
        return self._session.scalar(
            select(WorkspaceExportJob)
            .where(
                WorkspaceExportJob.workspace_id == workspace_id,
                WorkspaceExportJob.status == WorkspaceExportJobStatus.FAILED.value,
            )
            .order_by(WorkspaceExportJob.created_at.desc(), WorkspaceExportJob.id.desc())
            .limit(1)
        )

    def latest_archive_import_event(self, workspace_id: UUID) -> AuditEvent | None:
        return self._session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action == "workspace.archive_import.created",
            )
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(1)
        )

    def latest_archive_integrity_event(self, workspace_id: UUID) -> AuditEvent | None:
        return self._session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action == "workspace.archive_export_job.integrity_checked",
            )
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(1)
        )

    def latest_restore_drill_event(self, workspace_id: UUID) -> AuditEvent | None:
        return self._session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action == "workspace.archive_restore_drill.completed",
            )
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(1)
        )

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
        for collection, model in ARCHIVE_COVERAGE_MODELS.items():
            counts[collection] = int(
                self._session.scalar(
                    select(func.count(model.id)).where(model.workspace_id == workspace_id)
                )
                or 0
            )
        return counts

    def file_stats(self, workspace_id: UUID) -> dict[str, object]:
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

    def artifact_stats(self, workspace_id: UUID) -> dict[str, object]:
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
