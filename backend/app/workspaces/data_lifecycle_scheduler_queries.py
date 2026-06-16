from datetime import datetime
from uuid import UUID

from backend.app.audit.models import AuditEvent
from backend.app.exports.models import WorkspaceExportJob
from backend.app.workspaces.data_lifecycle_repository import WorkspaceDataLifecycleRepository
from backend.app.workspaces.models import Workspace


class ScheduledLifecycleQueryMixin:
    @property
    def _repo(self) -> WorkspaceDataLifecycleRepository:
        return WorkspaceDataLifecycleRepository(self._session)

    def _has_active_archive_export_job(self, workspace_id: UUID) -> bool:
        return self._repo.has_active_archive_export_job(workspace_id)

    def _latest_lifecycle_retention_run_at(self, workspace_id: UUID) -> datetime | None:
        return self._repo.latest_lifecycle_retention_run_at(workspace_id)

    def _record_lifecycle_schedule_event(
        self,
        *,
        workspace: Workspace,
        action: str,
        reason: str,
        metadata: dict[str, object],
    ) -> None:
        self._repo.record_lifecycle_schedule_event(
            workspace=workspace,
            action=action,
            reason=reason,
            metadata=metadata,
        )

    def _latest_export_job(self, workspace_id: UUID) -> WorkspaceExportJob | None:
        return self._repo.latest_export_job(workspace_id)

    def _latest_successful_archive_export(self, workspace_id: UUID) -> WorkspaceExportJob | None:
        return self._repo.latest_successful_archive_export(workspace_id)

    def _latest_restore_drill_event(self, workspace_id: UUID) -> AuditEvent | None:
        return self._repo.latest_restore_drill_event(workspace_id)
