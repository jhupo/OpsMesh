from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.services.exports import SUPPORTED_WORKSPACE_EXPORT_FORMAT
from backend.app.artifacts.models import Artifact
from backend.app.audit.models import AuditEvent
from backend.app.audit.service import AuditService
from backend.app.exports.models import WorkspaceExportJob
from backend.app.exports.status import WorkspaceExportJobStatus
from backend.app.files.models import FileAccessEvent, WorkspaceFile
from backend.app.workspaces.models import Workspace

RETENTION_DELETED_FILE_STATUS = "retention_deleted"


class WorkspaceDataLifecycleService:
    """Diagnose workspace export, backup, retention, and access-audit readiness."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_diagnostics(self, *, workspace_id: UUID) -> dict[str, object] | None:
        workspace = self._session.get(Workspace, workspace_id)
        if workspace is None:
            return None

        latest_job = self._latest_export_job(workspace_id)
        latest_success = self._latest_successful_archive_export(workspace_id)
        file_stats = self._file_stats(workspace_id)
        artifact_stats = self._artifact_stats(workspace_id)
        access_stats = self._access_stats(workspace_id)
        retention_policy = _retention_policy(workspace.settings)
        backup_policy = _backup_policy(workspace.settings, latest_job, latest_success)
        readiness = _readiness(retention_policy, backup_policy, latest_success)

        return {
            "workspace_id": workspace_id,
            "generated_at": datetime.now(UTC),
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
        latest_failed_job = self._latest_failed_export_job(workspace_id)
        job_stats = self._export_job_stats(workspace_id)
        retention_policy = _retention_policy(workspace.settings)
        backup_policy = _backup_policy(workspace.settings, latest_job, latest_success)
        restore_readiness = _restore_readiness(
            latest_success=latest_success,
            latest_import=latest_import,
            backup_policy=backup_policy,
            generated_at=generated_at,
            active_job_count=job_stats["active_job_count"],
        )

        return {
            "workspace_id": workspace_id,
            "generated_at": generated_at,
            "latest_successful_archive_export": _job_payload(latest_success),
            "latest_archive_import": _audit_event_payload(latest_import),
            "latest_failed_export_job": _job_payload(latest_failed_job),
            "export_jobs": job_stats,
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
            "warnings": _retention_warnings(policy, candidates),
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


def _retention_policy(settings: dict[str, object]) -> dict[str, object]:
    data_lifecycle = settings.get("data_lifecycle") if isinstance(settings, dict) else None
    lifecycle = data_lifecycle if isinstance(data_lifecycle, dict) else {}
    raw_policy = lifecycle.get("retention") if isinstance(lifecycle.get("retention"), dict) else {}
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
) -> dict[str, object]:
    data_lifecycle = settings.get("data_lifecycle") if isinstance(settings, dict) else None
    lifecycle = data_lifecycle if isinstance(data_lifecycle, dict) else {}
    raw_policy = lifecycle.get("backup") if isinstance(lifecycle.get("backup"), dict) else {}
    enabled = bool(raw_policy.get("enabled", False))
    warnings: list[str] = []
    if not enabled:
        warnings.append("backup_policy_not_enabled")
    if latest_success is None:
        warnings.append("no_successful_archive_export")
    return {
        "enabled": enabled,
        "source": "workspace.settings.data_lifecycle.backup",
        "schedule": raw_policy.get("schedule"),
        "target_type": raw_policy.get("target_type") or "manual_export",
        "target": raw_policy.get("target"),
        "last_export_job_status": latest_job.status if latest_job is not None else None,
        "last_successful_archive_export_at": (
            latest_success.completed_at if latest_success is not None else None
        ),
        "warnings": warnings,
    }


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
    latest_import: AuditEvent | None,
    backup_policy: dict[str, object],
    generated_at: datetime,
    active_job_count: object,
) -> dict[str, object]:
    blocked_reasons: list[str] = []
    warnings: list[str] = []
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
    if latest_import is None:
        blocked_reasons.append("no_archive_import_test_recorded")
    if isinstance(active_job_count, int) and active_job_count > 0:
        warnings.append("archive_export_jobs_in_progress")

    latest_archive_age_days = (
        _age_days(generated_at, latest_success.completed_at)
        if latest_success is not None and latest_success.completed_at is not None
        else None
    )
    return {
        "ready": not blocked_reasons,
        "blocked_reasons": blocked_reasons,
        "warnings": warnings,
        "downloadable_archive_available": (
            latest_success is not None
            and latest_success.status == WorkspaceExportJobStatus.COMPLETED.value
            and latest_success.storage_key is not None
        ),
        "latest_archive_age_days": latest_archive_age_days,
        "latest_archive_import_test_recorded": latest_import is not None,
        "latest_archive_import_tested_at": latest_import.created_at
        if latest_import is not None
        else None,
    }


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
    normalized_then = then if then.tzinfo is not None else then.replace(tzinfo=UTC)
    return max((now - normalized_then).days, 0)


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
