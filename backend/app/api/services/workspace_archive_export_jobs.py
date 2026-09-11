from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.api.schemas.exports import (
    WorkspaceArchiveExportRequest,
    WorkspaceArchiveRestoreDrillRequest,
)
from backend.app.api.services.workspace_archive_export_builder import WorkspaceArchiveExportBuilder
from backend.app.api.services.workspace_archive_export_repository import (
    WorkspaceArchiveExportJobRepository,
)
from backend.app.api.services.workspace_archive_integrity import WorkspaceArchiveIntegrityService
from backend.app.api.services.workspace_archive_restore_drill import (
    WorkspaceArchiveRestoreDrillService,
)
from backend.app.observability.audit_service import AuditService
from backend.app.exports.models import WorkspaceExportJob
from backend.app.exports.status import WorkspaceExportJobStatus
from backend.app.files.storage import ObjectStorage
from backend.app.files.storage_transactions import CompensatingObjectStorageWrites
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue.redis_queue import RedisQueue
from backend.app.workspaces.models import Workspace


class WorkspaceArchiveExportJobService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._jobs = WorkspaceArchiveExportJobRepository(session)

    def create_archive_export_job(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        request: WorkspaceArchiveExportRequest,
        queue: RedisQueue,
    ) -> WorkspaceExportJob:
        export_job = WorkspaceExportJob(
            workspace_id=workspace.id,
            created_by_user_id=user_id,
            export_type="workspace_archive",
            status=WorkspaceExportJobStatus.QUEUED.value,
            request=request.model_dump(mode="json"),
            job_metadata={},
        )
        self._session.add(export_job)
        self._session.flush()
        enqueued = queue.enqueue(
            JobPayload(
                workspace_id=workspace.id,
                job_type=JobType.WORKSPACE_ARCHIVE_EXPORT,
                resource_id=export_job.id,
                requested_by_user_id=user_id,
                idempotency_key=f"workspace.archive_export:{workspace.id}:{export_job.id}",
                max_attempts=2,
            )
        )
        if not enqueued:
            export_job.status = WorkspaceExportJobStatus.FAILED.value
            export_job.error = "Failed to enqueue archive export job"
            export_job.completed_at = datetime.now(UTC)
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=user_id,
            action="workspace.archive_export_job.created",
            target_type="workspace_export_job",
            target_id=export_job.id,
            metadata={"enqueued": enqueued},
        )
        self._session.commit()
        self._session.refresh(export_job)
        return export_job

    def get_export_job(self, *, workspace_id: UUID, job_id: UUID) -> WorkspaceExportJob | None:
        return self._jobs.get(workspace_id=workspace_id, job_id=job_id)

    def read_export_job_content(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        storage: ObjectStorage,
    ) -> tuple[WorkspaceExportJob, bytes]:
        return self._jobs.read_completed_content(
            workspace_id=workspace_id,
            job_id=job_id,
            storage=storage,
        )

    def fail_archive_export_job(
        self,
        *,
        job: JobPayload,
        error: str,
    ) -> WorkspaceExportJob | None:
        export_job = self._jobs.get(workspace_id=job.workspace_id, job_id=job.resource_id)
        if export_job is None:
            return None
        if export_job.status == WorkspaceExportJobStatus.COMPLETED.value:
            return export_job
        export_job.status = WorkspaceExportJobStatus.FAILED.value
        export_job.error = error[:1000]
        export_job.completed_at = datetime.now(UTC)
        self._record_job_failure(job, export_job)
        self._session.commit()
        self._session.refresh(export_job)
        return export_job

    def verify_archive_export_job(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        user_id: UUID,
        storage: ObjectStorage,
    ) -> dict[str, object]:
        return WorkspaceArchiveIntegrityService(self._session).verify_job(
            workspace_id=workspace_id,
            job_id=job_id,
            user_id=user_id,
            storage=storage,
        )

    def run_archive_restore_drill(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        job_id: UUID,
        request: WorkspaceArchiveRestoreDrillRequest,
        storage: ObjectStorage,
    ) -> dict[str, object]:
        return WorkspaceArchiveRestoreDrillService(self._session).run(
            workspace=workspace,
            user_id=user_id,
            job_id=job_id,
            request=request,
            storage=storage,
        )

    def run_archive_export_job(
        self,
        *,
        job: JobPayload,
        storage: ObjectStorage,
    ) -> WorkspaceExportJob:
        export_job = self._require_export_job(job)
        if export_job.status == WorkspaceExportJobStatus.COMPLETED.value:
            return export_job
        workspace = self._require_workspace(job, export_job)
        self._mark_running(export_job)
        try:
            return self._build_and_complete_export(job, export_job, workspace, storage)
        except Exception as exc:
            self._session.rollback()
            self._mark_failed_after_worker_error(job, workspace, exc)
            raise

    def _require_export_job(self, job: JobPayload) -> WorkspaceExportJob:
        export_job = self._jobs.get(workspace_id=job.workspace_id, job_id=job.resource_id)
        if export_job is None:
            raise ValueError("Export job not found")
        return export_job

    def _require_workspace(self, job: JobPayload, export_job: WorkspaceExportJob) -> Workspace:
        workspace = self._session.get(Workspace, job.workspace_id)
        if workspace is None:
            raise ValueError("Workspace not found")
        if export_job.workspace_id != workspace.id:
            raise ValueError("Export job workspace mismatch")
        return workspace

    def _mark_running(self, export_job: WorkspaceExportJob) -> None:
        export_job.status = WorkspaceExportJobStatus.RUNNING.value
        export_job.started_at = datetime.now(UTC)
        export_job.error = None
        self._session.commit()

    def _build_and_complete_export(
        self,
        job: JobPayload,
        export_job: WorkspaceExportJob,
        workspace: Workspace,
        storage: ObjectStorage,
    ) -> WorkspaceExportJob:
        request = WorkspaceArchiveExportRequest.model_validate(export_job.request)
        actor_user_id = job.requested_by_user_id or workspace.owner_user_id
        result = WorkspaceArchiveExportBuilder(self._session).build_archive_export(
            workspace=workspace,
            user_id=actor_user_id,
            request=request,
            storage=storage,
        )
        storage_key = f"workspaces/{workspace.id}/exports/{export_job.id}/archive.zip"
        writes = CompensatingObjectStorageWrites(storage)
        try:
            writes.write_new(storage_key, result.content)
        except Exception:
            self._session.rollback()
            raise
        export_job.status = WorkspaceExportJobStatus.COMPLETED.value
        export_job.storage_key = storage_key
        export_job.filename = result.filename
        export_job.content_type = result.content_type
        export_job.size_bytes = len(result.content)
        export_job.checksum_sha256 = sha256(result.content).hexdigest()
        export_job.completed_at = datetime.now(UTC)
        export_job.job_metadata = {
            **export_job.job_metadata,
            "manifest_counts": result.manifest_counts,
            "skipped_objects": result.skipped_objects,
        }
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=actor_user_id,
            action="workspace.archive_export_job.completed",
            target_type="workspace_export_job",
            target_id=export_job.id,
            metadata={
                "filename": result.filename,
                "size_bytes": len(result.content),
                "skipped_objects": result.skipped_objects,
            },
        )
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            writes.compensate()
            raise
        writes.complete()
        self._session.refresh(export_job)
        return export_job

    def _mark_failed_after_worker_error(
        self,
        job: JobPayload,
        workspace: Workspace,
        exc: Exception,
    ) -> None:
        failed_job = self._jobs.get(workspace_id=job.workspace_id, job_id=job.resource_id)
        if failed_job is None:
            raise exc
        failed_job.status = WorkspaceExportJobStatus.FAILED.value
        failed_job.error = str(exc)[:1000]
        failed_job.completed_at = datetime.now(UTC)
        self._record_job_failure(job, failed_job, workspace=workspace)
        self._session.commit()

    def _record_job_failure(
        self,
        job: JobPayload,
        export_job: WorkspaceExportJob,
        *,
        workspace: Workspace | None = None,
    ) -> None:
        actor_user_id = job.requested_by_user_id
        workspace = workspace or self._session.get(Workspace, job.workspace_id)
        if actor_user_id is None and workspace is not None:
            actor_user_id = workspace.owner_user_id
        if actor_user_id is None:
            return
        AuditService(self._session).record_user_action(
            workspace_id=job.workspace_id,
            user_id=actor_user_id,
            action="workspace.archive_export_job.failed",
            target_type="workspace_export_job",
            target_id=export_job.id,
            metadata={"error": export_job.error},
        )
