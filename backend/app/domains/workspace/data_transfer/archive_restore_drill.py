from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domains.workspace.data_transfer.contracts import (
    WorkspaceArchiveImportRequest,
    WorkspaceArchiveRestoreDrillRequest,
)
from backend.app.domains.workspace.data_transfer.importers.archive import (
    WorkspaceArchiveImportService,
)
from backend.app.domains.workspace.data_transfer.importers.preview import (
    _import_preview_audit_metadata,
)
from backend.app.domains.workspace.data_transfer.repository import (
    WorkspaceArchiveExportJobRepository,
)
from backend.app.domains.workspace.storage.artifact_models import Artifact
from backend.app.domains.workspace.storage.models import WorkspaceFile
from backend.app.domains.workspace.storage.storage import ObjectStorage
from backend.app.domains.workspace.tenants.models import Workspace
from backend.app.observability.audit.service import AuditService


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
        drill_workspace = Workspace(
            owner_user_id=workspace.owner_user_id,
            name=f"{request.name_prefix}{workspace.name}"[:160],
            slug=f"restore-drill-{export_job.id.hex[:16]}",
            settings={"restore_drill": True},
            status="active",
        )
        self._session.add(drill_workspace)
        self._session.flush()
        imported_files: list[str] = []
        imported_artifacts: list[str] = []
        try:
            preview = WorkspaceArchiveImportService(self._session).import_archive(
                workspace=drill_workspace,
                user_id=user_id,
                archive_bytes=archive_bytes,
                request=WorkspaceArchiveImportRequest(
                    dry_run=False,
                    import_agents=request.import_agents,
                    import_teams=request.import_teams,
                    import_tasks=request.import_tasks,
                    import_runtime_spaces=request.import_runtime_spaces,
                    import_skill_installs=request.import_skill_installs,
                    import_memory=request.import_memory,
                    import_projects=request.import_projects,
                    import_file_bytes=request.import_file_bytes,
                    import_artifact_bytes=request.import_artifact_bytes,
                    name_prefix=request.name_prefix,
                    max_items_per_collection=request.max_items_per_collection,
                    max_bytes_per_object=request.max_bytes_per_object,
                    max_total_bytes=request.max_total_bytes,
                ),
                storage=storage,
            )
            imported_files = list(
                self._session.scalars(
                    select(WorkspaceFile.storage_key).where(
                        WorkspaceFile.workspace_id == drill_workspace.id
                    )
                )
            )
            imported_artifacts = list(
                self._session.scalars(
                    select(Artifact.storage_key).where(Artifact.workspace_id == drill_workspace.id)
                )
            )
            passed = (
                len(preview.required_resolutions) == 0
                and preview.skipped_counts.get("files", 0) == 0
                and preview.skipped_counts.get("artifacts", 0) == 0
            )
        except Exception:
            self._session.rollback()
            raise
        finally:
            for storage_key in [*imported_files, *imported_artifacts]:
                storage.delete(storage_key)
            existing_workspace = self._session.get(Workspace, drill_workspace.id)
            if existing_workspace is not None:
                self._session.delete(existing_workspace)
            self._session.commit()
        audit_metadata = _import_preview_audit_metadata(preview)
        metadata = {
            "source_export_job_id": str(export_job.id),
            "source_export_completed_at": (
                export_job.completed_at.isoformat() if export_job.completed_at is not None else None
            ),
            "passed": passed,
            "disposable_workspace_id": str(drill_workspace.id),
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
