from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.artifacts.models import Artifact
from backend.app.observability.audit_service import AuditService
from backend.app.files.models import WorkspaceFile
from backend.app.workspaces.data_lifecycle_policy import (
    _candidate_counts,
    _candidate_payload,
    _retention_blocked_reasons,
    _retention_days,
    _retention_policy,
    _retention_recommended_actions,
    _retention_warnings,
)
from backend.app.workspaces.data_lifecycle_repository import WorkspaceDataLifecycleRepository
from backend.app.workspaces.models import Workspace

RETENTION_DELETED_FILE_STATUS = "retention_deleted"


class WorkspaceRetentionService:
    def __init__(self, session: Session) -> None:
        self._session = session

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
        latest_success = WorkspaceDataLifecycleRepository(
            self._session
        ).latest_successful_archive_export(workspace_id)
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
        jobs = WorkspaceDataLifecycleRepository(
            self._session
        ).retention_export_job_candidates(
            workspace_id=workspace_id,
            cutoff=cutoff,
            limit=limit,
        )
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
