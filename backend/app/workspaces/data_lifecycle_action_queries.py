from uuid import UUID

from backend.app.audit.models import AuditEvent
from backend.app.exports.models import WorkspaceExportJob
from backend.app.workspaces.data_lifecycle_repository import WorkspaceDataLifecycleRepository


class RecoveryActionQueryMixin:
    @property
    def _repo(self) -> WorkspaceDataLifecycleRepository:
        return WorkspaceDataLifecycleRepository(self._session)

    def _has_active_archive_export_job(self, workspace_id: UUID) -> bool:
        return self._repo.has_active_archive_export_job(workspace_id)

    def _active_archive_export_job_count(self, workspace_id: UUID) -> int:
        return self._repo.active_archive_export_job_count(workspace_id)

    def _latest_successful_archive_export(self, workspace_id: UUID) -> WorkspaceExportJob | None:
        return self._repo.latest_successful_archive_export(workspace_id)

    def _latest_archive_integrity_event(self, workspace_id: UUID) -> AuditEvent | None:
        return self._repo.latest_archive_integrity_event(workspace_id)
