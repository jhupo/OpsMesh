from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.files.storage import ObjectStorage
from backend.app.workers.queue.redis_queue import RedisQueue
from backend.app.workspaces.data_lifecycle_actions import WorkspaceRecoveryActionService
from backend.app.workspaces.data_lifecycle_diagnostics import WorkspaceLifecycleDiagnosticsService
from backend.app.workspaces.data_lifecycle_retention import WorkspaceRetentionService
from backend.app.workspaces.data_lifecycle_scheduler import WorkspaceScheduledLifecycleService
from backend.app.workspaces.data_lifecycle_summary import ScheduledLifecycleSummary


class WorkspaceDataLifecycleService:
    """Coordinate workspace data lifecycle diagnostics and maintenance actions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_diagnostics(self, *, workspace_id: UUID) -> dict[str, object] | None:
        return WorkspaceLifecycleDiagnosticsService(self._session).get_diagnostics(
            workspace_id=workspace_id
        )

    def get_recovery_readiness(self, *, workspace_id: UUID) -> dict[str, object] | None:
        return WorkspaceLifecycleDiagnosticsService(self._session).get_recovery_readiness(
            workspace_id=workspace_id
        )

    def apply_recovery_readiness_actions(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        queue: RedisQueue,
        storage: ObjectStorage | None = None,
        dry_run: bool = True,
        actions: list[str] | None = None,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        diagnostics = WorkspaceLifecycleDiagnosticsService(self._session)
        return WorkspaceRecoveryActionService(self._session).apply_recovery_readiness_actions(
            workspace_id=workspace_id,
            user_id=user_id,
            queue=queue,
            storage=storage,
            dry_run=dry_run,
            actions=actions,
            reason=reason,
            metadata=metadata,
            readiness=diagnostics.get_recovery_readiness(workspace_id=workspace_id),
        )

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
        return WorkspaceRetentionService(self._session).preview_retention(
            workspace_id=workspace_id,
            user_id=user_id,
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
        return WorkspaceRetentionService(self._session).apply_retention(
            workspace_id=workspace_id,
            user_id=user_id,
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
        storage: ObjectStorage | None = None,
        workspace_id: UUID | None = None,
        limit: int = 100,
    ) -> ScheduledLifecycleSummary:
        return WorkspaceScheduledLifecycleService(self._session).run_scheduled_lifecycle(
            queue=queue,
            storage=storage,
            workspace_id=workspace_id,
            limit=limit,
        )
