from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditService
from opsmesh.resources.artifacts.models import Artifact
from opsmesh.resources.files.models import WorkspaceFile
from opsmesh.resources.storage.backends import ObjectStorage
from opsmesh.resources.transfers.contracts import (
    WorkspaceArchiveImportRequest,
    WorkspaceArchiveRestoreDrillRequest,
    WorkspaceImportResponse,
    WorkspaceRestoreDrillResponse,
)
from opsmesh.resources.transfers.importers.archive import WorkspaceArchiveImportService
from opsmesh.resources.transfers.importers.preview import _import_preview_audit_metadata
from opsmesh.resources.transfers.models import WorkspaceRestoreDrillOperation
from opsmesh.resources.transfers.operation_identity import (
    authorize_archive_identity,
    authorize_archive_source,
)
from opsmesh.resources.transfers.repository import WorkspaceArchiveExportJobRepository
from opsmesh.resources.transfers.restore_operations import (
    RestoreDrillOperationService,
    restore_drill_engine,
    restore_drill_lock,
)
from opsmesh.resources.transfers.restore_storage import RestoreDrillStorage
from opsmesh.runtime.temporal.contracts import ExecutionRequest
from opsmesh.shared.errors import DomainError
from opsmesh.workspaces.management.models import Workspace


class WorkspaceArchiveRestoreDrillService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._jobs = WorkspaceArchiveExportJobRepository(session)

    def run(
        self,
        *,
        execution: ExecutionRequest,
        storage: ObjectStorage,
    ) -> dict[str, object]:
        engine = restore_drill_engine(self._session)
        with restore_drill_lock(engine, execution.workspace_id):
            operations = RestoreDrillOperationService(self._session)
            operation = self._session.scalar(
                select(WorkspaceRestoreDrillOperation)
                .where(
                    WorkspaceRestoreDrillOperation.workspace_id == execution.workspace_id,
                    WorkspaceRestoreDrillOperation.id == execution.resource_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if operation is None:
                raise ValueError("Restore drill operation not found")
            if operation.status == "completed" and operation.result is not None:
                return operation.result
            if operation.status != "queued":
                if operation.status == "running":
                    operations.finish(
                        execution.workspace_id,
                        operation.id,
                        "unknown",
                        "restore_drill_outcome_unknown",
                    )
                raise DomainError(
                    "Restore drill cannot replay an uncertain or settled operation",
                    code="restore_drill_replay_refused",
                    status_code=409,
                )
            user = authorize_archive_identity(
                self._session, execution.workspace_id, operation.execution_identity
            )
            if user.user_id != execution.requested_by_user_id or user.user_id != operation.user_id:
                raise ValueError("Restore drill initiating identity mismatch")
            workspace = self._session.get(Workspace, execution.workspace_id)
            if workspace is None or workspace.status != "active":
                raise ValueError("Workspace is unavailable")
            request = WorkspaceArchiveRestoreDrillRequest.model_validate(operation.request)
            user_id, job_id = user.user_id, operation.export_job_id
            operation.status = "running"
            self._session.commit()
            operation_id, temporary_id = operation.id, operation.temporary_workspace_id
            receipts = RestoreDrillStorage(engine, storage, workspace.id, operation_id)
            try:
                result = self._execute(
                    workspace=workspace,
                    user_id=user_id,
                    job_id=job_id,
                    request=request,
                    storage=receipts,
                    temporary_workspace_id=temporary_id,
                    execution_identity=operation.execution_identity,
                )
                receipts.cleanup()
                authorize_archive_identity(
                    self._session, workspace.id, operation.execution_identity
                )
            except BaseException:
                self._session.rollback()
                try:
                    receipts.cleanup()
                except Exception:
                    operations.finish(
                        workspace.id, operation_id, "unknown", "restore_drill_cleanup_unknown"
                    )
                    raise DomainError(
                        "Restore drill cleanup requires explicit reconciliation",
                        code="restore_drill_cleanup_unknown",
                        status_code=409,
                    ) from None
                operations.finish(workspace.id, operation_id, "failed", "restore_drill_failed")
                raise
            payload = WorkspaceRestoreDrillResponse.model_validate(
                {**result, "operation_id": operation_id}
            ).model_dump(mode="json")
            AuditService(self._session).record_user_action(
                workspace_id=workspace.id,
                user_id=user_id,
                action="workspace.archive_restore_drill.completed",
                target_type="workspace_export_job",
                target_id=job_id,
                metadata=payload["metadata"],
            )
            if operation.origin != "user":
                AuditService(self._session).record_user_action(
                    workspace_id=workspace.id,
                    user_id=user_id,
                    action="workspace.lifecycle.restore_drill_completed"
                    if operation.origin == "scheduled"
                    else "workspace.recovery_readiness.restore_import_test_completed",
                    target_type="workspace_export_job",
                    target_id=job_id,
                    metadata={
                        "operation_id": str(operation_id),
                        "source_export_job_id": str(job_id),
                        "passed": payload["passed"],
                        "required_resolution_count": payload["required_resolution_count"],
                        "suggested_resolution_count": payload["suggested_resolution_count"],
                        "conflict_counts": payload["conflict_counts"],
                    },
                )
            operations.finish(workspace.id, operation_id, "completed", None, result=payload)
            return payload

    def _execute(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        job_id: UUID,
        request: WorkspaceArchiveRestoreDrillRequest,
        storage: ObjectStorage,
        temporary_workspace_id: UUID,
        execution_identity: dict[str, object],
    ) -> dict[str, object]:
        export_job = self._jobs.get(workspace_id=workspace.id, job_id=job_id)
        if export_job is None or export_job.status != "completed" or export_job.storage_key is None:
            raise ValueError("A completed archive export is required")
        key, checksum = export_job.storage_key, export_job.checksum_sha256
        completed_at = export_job.completed_at.isoformat() if export_job.completed_at else None
        self._session.commit()
        archive_bytes = storage.read(key)
        self._session.expire(export_job)
        if (
            export_job.status != "completed"
            or export_job.storage_key != key
            or export_job.checksum_sha256 != checksum
        ):
            raise ValueError("Archive export changed during restore drill")
        authorize_archive_source(
            self._session, workspace.id, export_job.execution_identity, execution_identity
        )
        drilled_at = datetime.now(UTC)
        # The authorized source session must retain its tenant scope. Restore only into a
        # newly-created disposable workspace in a separate session and rollback its savepoint,
        # including commits performed by the normal archive importer.
        connection = self._session.connection()
        savepoint = connection.begin_nested()
        try:
            with Session(
                bind=connection, join_transaction_mode="create_savepoint"
            ) as drill_session:
                preview, drill_workspace_id = self._restore(
                    drill_session,
                    workspace=workspace,
                    user_id=user_id,
                    job_id=export_job.id,
                    archive_bytes=archive_bytes,
                    request=request,
                    storage=storage,
                    temporary_workspace_id=temporary_workspace_id,
                )
        finally:
            savepoint.rollback()
        passed = (
            len(preview.required_resolutions) == 0
            and preview.skipped_counts.get("files", 0) == 0
            and preview.skipped_counts.get("artifacts", 0) == 0
        )
        audit_metadata = _import_preview_audit_metadata(preview)
        metadata = {
            "source_export_job_id": str(export_job.id),
            "source_export_completed_at": completed_at,
            "passed": passed,
            "disposable_workspace_id": str(drill_workspace_id),
            **audit_metadata,
        }
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

    @staticmethod
    def _restore(
        session: Session,
        *,
        workspace: Workspace,
        user_id: UUID,
        job_id: UUID,
        archive_bytes: bytes,
        request: WorkspaceArchiveRestoreDrillRequest,
        storage: ObjectStorage,
        temporary_workspace_id: UUID,
    ) -> tuple[WorkspaceImportResponse, UUID]:
        drill_workspace = Workspace(
            id=temporary_workspace_id,
            owner_user_id=workspace.owner_user_id,
            name=f"{request.name_prefix}{workspace.name}"[:160],
            slug=f"restore-drill-{job_id.hex[:16]}",
            settings={"restore_drill": True},
            status="active",
        )
        session.add(drill_workspace)
        session.flush()
        imported_files: list[str] = []
        imported_artifacts: list[str] = []
        try:
            preview = WorkspaceArchiveImportService(session).import_archive(
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
                session.scalars(
                    select(WorkspaceFile.storage_key).where(
                        WorkspaceFile.workspace_id == drill_workspace.id
                    )
                )
            )
            imported_artifacts = list(
                session.scalars(
                    select(Artifact.storage_key).where(Artifact.workspace_id == drill_workspace.id)
                )
            )
            return preview, drill_workspace.id
        finally:
            for storage_key in [*imported_files, *imported_artifacts]:
                storage.delete(storage_key)
