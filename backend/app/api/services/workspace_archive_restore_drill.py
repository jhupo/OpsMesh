from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.api.schemas.exports import (
    WorkspaceArchiveImportRequest,
    WorkspaceArchiveRestoreDrillRequest,
)
from backend.app.api.services.workspace_archive_export_repository import (
    WorkspaceArchiveExportJobRepository,
)
from backend.app.api.services.workspace_archive_import import WorkspaceArchiveImportService
from backend.app.api.services.workspace_import_preview import _import_preview_audit_metadata
from backend.app.audit.service import AuditService
from backend.app.files.storage import ObjectStorage
from backend.app.workspaces.models import Workspace


class WorkspaceArchiveRestoreDrillService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._jobs = WorkspaceArchiveExportJobRepository(session)

    def run(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        job_id: UUID,
        request: WorkspaceArchiveRestoreDrillRequest,
        storage: ObjectStorage,
    ) -> dict[str, object]:
        export_job, archive_bytes = self._jobs.read_completed_content(
            workspace_id=workspace.id,
            job_id=job_id,
            storage=storage,
        )
        drilled_at = datetime.now(UTC)
        preview = WorkspaceArchiveImportService(self._session).import_archive(
            workspace=workspace,
            user_id=user_id,
            archive_bytes=archive_bytes,
            request=WorkspaceArchiveImportRequest(
                dry_run=True,
                import_agents=request.import_agents,
                import_teams=request.import_teams,
                import_tasks=request.import_tasks,
                import_runtime_spaces=request.import_runtime_spaces,
                import_skill_installs=request.import_skill_installs,
                import_file_bytes=request.import_file_bytes,
                import_artifact_bytes=request.import_artifact_bytes,
                name_prefix=request.name_prefix,
                max_items_per_collection=request.max_items_per_collection,
                max_bytes_per_object=request.max_bytes_per_object,
                max_total_bytes=request.max_total_bytes,
            ),
            storage=storage,
        )
        audit_metadata = _import_preview_audit_metadata(preview)
        passed = audit_metadata["required_resolution_count"] == 0
        metadata = {
            "source_export_job_id": str(export_job.id),
            "source_export_completed_at": (
                export_job.completed_at.isoformat() if export_job.completed_at is not None else None
            ),
            "passed": passed,
            **audit_metadata,
        }
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=user_id,
            action="workspace.archive_restore_drill.completed",
            target_type="workspace_export_job",
            target_id=export_job.id,
            metadata=metadata,
        )
        self._session.commit()
        return {
            "workspace_id": workspace.id,
            "job_id": export_job.id,
            "drilled_at": drilled_at,
            "passed": passed,
            "required_resolution_count": audit_metadata["required_resolution_count"],
            "suggested_resolution_count": audit_metadata["suggested_resolution_count"],
            "conflict_counts": audit_metadata["conflict_counts"],
            "import_preview": preview,
            "metadata": metadata,
        }
