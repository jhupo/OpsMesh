from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditService
from opsmesh.identity.authorization.resource_queries import (
    execution_resource_queries,
    resource_query_scope,
)
from opsmesh.platform.updates.admission import require_execution_admission
from opsmesh.resources.storage.backends import ObjectStorage
from opsmesh.resources.storage.transactions import (
    CompensatingObjectStorageWrites,
    ObjectStorageCompensationError,
)
from opsmesh.resources.transfers.archive_builder import WorkspaceArchiveExportBuilder
from opsmesh.resources.transfers.archive_ownership import archive_export_ownership
from opsmesh.resources.transfers.contracts import (
    SUPPORTED_WORKSPACE_EXPORT_FORMAT,
    WorkspaceArchiveExportRequest,
)
from opsmesh.resources.transfers.models import WorkspaceExportJob, WorkspaceExportJobStatus
from opsmesh.resources.transfers.operation_identity import (
    authorize_archive_identity,
    authorize_archive_source,
    capture_archive_identity,
)
from opsmesh.resources.transfers.repository import WorkspaceArchiveExportJobRepository
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.outbox import stage_execution
from opsmesh.shared.errors import DomainError
from opsmesh.shared.security.redaction import redact_sensitive_text
from opsmesh.workspaces.management.models import Workspace


def _installed_release() -> str | None:
    try:
        return version("opsmesh")
    except PackageNotFoundError:
        return None


def _schema_revision(session: Session) -> str | None:
    configured = os.environ.get("OPSMESH_SCHEMA_REVISION")
    if configured:
        return configured
    if session.scalar(select(func.to_regclass("alembic_version"))) is None:
        return None
    value = session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    return str(value) if value is not None else None


def _retention_days(settings: dict[str, object]) -> int | None:
    lifecycle = settings.get("data_lifecycle") if isinstance(settings, dict) else None
    retention = lifecycle.get("retention") if isinstance(lifecycle, dict) else None
    if not isinstance(retention, dict):
        return None
    for key in ("export_job_retention_days", "default_retention_days"):
        value = retention.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return None


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
    ) -> WorkspaceExportJob:
        require_execution_admission(self._session)
        current = self._session.scalar(
            select(Workspace)
            .where(Workspace.id == workspace.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if current is None or current.status != "active":
            raise ValueError("Workspace is unavailable")
        jobs = WorkspaceExportJob.__table__
        # Admission concerns all workspace writers, including other users' private facts.
        if (
            self._session.scalar(
                select(jobs.c.id)
                .where(
                    jobs.c.workspace_id == workspace.id,
                    jobs.c.export_type == "workspace_archive",
                    jobs.c.status.in_({"queued", "running", "unknown"}),
                )
                .limit(1)
            )
            is not None
        ):
            raise DomainError(
                "A workspace archive export is already active",
                code="archive_export_active",
                status_code=409,
            )
        export_job = WorkspaceExportJob(
            workspace_id=workspace.id,
            created_by_user_id=user_id,
            export_type="workspace_archive",
            status=WorkspaceExportJobStatus.QUEUED.value,
            request=request.model_dump(mode="json"),
            execution_identity=capture_archive_identity(self._session, workspace.id, user_id),
            job_metadata={},
            source_release=_installed_release(),
            source_schema_revision=_schema_revision(self._session),
        )
        self._session.add(export_job)
        self._session.flush()
        workflow_id = stage_execution(
            self._session,
            ExecutionRequest(
                execution_id=export_job.id,
                workspace_id=workspace.id,
                kind=ExecutionKind.ARCHIVE_EXPORT,
                resource_id=export_job.id,
                requested_by_user_id=user_id,
            ),
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=user_id,
            action="workspace.archive_export_job.created",
            target_type="workspace_export_job",
            target_id=export_job.id,
            metadata={"workflow_id": workflow_id},
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
        scope = resource_query_scope(self._session)
        if scope is None:
            raise ValueError("Archive download requires an authenticated resource scope")
        identity = capture_archive_identity(self._session, workspace_id, scope.user.user_id)
        source = self._jobs.get(workspace_id=workspace_id, job_id=job_id)
        if source is None:
            raise FileNotFoundError("Export job not found")
        authorize_archive_source(self._session, workspace_id, source.execution_identity, identity)
        result = self._jobs.read_completed_content(
            workspace_id=workspace_id,
            job_id=job_id,
            storage=storage,
        )
        authorize_archive_source(self._session, workspace_id, source.execution_identity, identity)
        return result

    def settle_interrupted_export(
        self,
        *,
        request: ExecutionRequest,
    ) -> WorkspaceExportJob | None:
        export_job = self._session.scalar(
            select(WorkspaceExportJob)
            .where(
                WorkspaceExportJob.workspace_id == request.workspace_id,
                WorkspaceExportJob.id == request.resource_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if export_job is None:
            return None
        if export_job.status not in {"queued", "running"}:
            return export_job
        export_job.status = "unknown" if export_job.status == "running" else "failed"
        export_job.error = (
            "archive_export_outcome_unknown"
            if export_job.status == "unknown"
            else "archive_export_not_started"
        )
        export_job.completed_at = datetime.now(UTC)
        self._record_job_failure(request, export_job)
        self._session.commit()
        self._session.refresh(export_job)
        return export_job

    def run_archive_export_job(
        self,
        *,
        request: ExecutionRequest,
        storage: ObjectStorage,
    ) -> WorkspaceExportJob:
        with archive_export_ownership(self._session, request.resource_id):
            export_job = self._require_export_job(request)
            if export_job.status == WorkspaceExportJobStatus.COMPLETED.value:
                return export_job
            if export_job.status != "queued":
                if export_job.status == "running":
                    self.settle_interrupted_export(request=request)
                raise DomainError(
                    "Archive export cannot replay an uncertain or settled operation",
                    code="archive_export_replay_refused",
                    status_code=409,
                )
            workspace = self._require_workspace(request, export_job)
            user = authorize_archive_identity(
                self._session, workspace.id, export_job.execution_identity
            )
            if user.user_id != request.requested_by_user_id:
                raise ValueError("Archive export initiating identity mismatch")
            self._mark_running(export_job)
            try:
                return self._build_and_complete_export(request, export_job, workspace, storage)
            except Exception as exc:
                self._session.rollback()
                self._mark_failed_after_worker_error(request, exc)
                raise

    def _require_export_job(self, request: ExecutionRequest) -> WorkspaceExportJob:
        export_job = self._session.scalar(
            select(WorkspaceExportJob)
            .where(
                WorkspaceExportJob.workspace_id == request.workspace_id,
                WorkspaceExportJob.id == request.resource_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if export_job is None:
            raise ValueError("Export job not found")
        return export_job

    def _require_workspace(
        self, request: ExecutionRequest, export_job: WorkspaceExportJob
    ) -> Workspace:
        workspace = self._session.get(Workspace, request.workspace_id)
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
        execution: ExecutionRequest,
        export_job: WorkspaceExportJob,
        workspace: Workspace,
        storage: ObjectStorage,
    ) -> WorkspaceExportJob:
        request = WorkspaceArchiveExportRequest.model_validate(export_job.request)
        actor_user_id = execution.requested_by_user_id
        if actor_user_id is None:
            raise ValueError("Archive export requires an authorized user")
        actor = authorize_archive_identity(
            self._session, workspace.id, export_job.execution_identity
        )
        with execution_resource_queries(self._session, workspace.id, actor):
            result = WorkspaceArchiveExportBuilder(self._session).build_archive_export(
                workspace=workspace,
                user_id=actor_user_id,
                request=request,
                storage=storage,
            )
        storage_key = f"workspaces/{workspace.id}/exports/{export_job.id}/archive.zip"
        authorize_archive_identity(self._session, workspace.id, export_job.execution_identity)
        self._session.commit()
        writes = CompensatingObjectStorageWrites(storage)
        try:
            writes.write_new(storage_key, result.content)
        except Exception:
            self._session.rollback()
            raise
        try:
            authorize_archive_identity(self._session, workspace.id, export_job.execution_identity)
        except Exception:
            self._session.rollback()
            writes.compensate()
            raise
        export_job.status = WorkspaceExportJobStatus.COMPLETED.value
        export_job.storage_key = storage_key
        export_job.filename = result.filename
        export_job.content_type = result.content_type
        export_job.size_bytes = len(result.content)
        export_job.checksum_sha256 = sha256(result.content).hexdigest()
        export_job.manifest_checksum_sha256 = result.manifest_checksum_sha256
        export_job.verification_status = "unverified"
        export_job.completed_at = datetime.now(UTC)
        retention_days = _retention_days(workspace.settings)
        if retention_days is not None:
            export_job.retention_until = export_job.completed_at + timedelta(days=retention_days)
        export_job.job_metadata = {
            **export_job.job_metadata,
            "manifest_counts": result.manifest_counts,
            "skipped_objects": result.skipped_objects,
            "object_inventory": result.object_inventory,
            "format_version": SUPPORTED_WORKSPACE_EXPORT_FORMAT,
            "sensitive_fields_policy": "secrets-excluded-redacted",
            "source_release": export_job.source_release,
            "source_schema_revision": export_job.source_schema_revision,
            "retention_days": retention_days,
        }
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=actor_user_id,
            action="workspace.archive_export.created",
            target_type="workspace",
            target_id=workspace.id,
            metadata={
                "included_file_bytes": request.include_file_bytes,
                "included_artifact_bytes": request.include_artifact_bytes,
                "skipped_objects": result.skipped_objects,
            },
        )
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
            # A lost COMMIT acknowledgement must not delete an already published archive.
            try:
                self._session.rollback()
                settled = self._require_export_job(execution)
            except Exception as probe_error:
                raise DomainError(
                    "Archive export commit outcome cannot be confirmed",
                    code="archive_export_outcome_unknown",
                    status_code=503,
                ) from probe_error
            if settled.status == WorkspaceExportJobStatus.COMPLETED.value:
                writes.complete()
                return settled
            self._session.rollback()
            writes.compensate()
            raise
        writes.complete()
        self._session.refresh(export_job)
        return export_job

    def _mark_failed_after_worker_error(
        self,
        request: ExecutionRequest,
        exc: Exception,
    ) -> None:
        failed_job = self._jobs.get(workspace_id=request.workspace_id, job_id=request.resource_id)
        if failed_job is None:
            raise exc
        if failed_job.status == "completed":
            return
        failed_job.status = (
            "unknown"
            if isinstance(exc, ObjectStorageCompensationError)
            or isinstance(exc, DomainError)
            and exc.code == "archive_export_outcome_unknown"
            else "failed"
        )
        failed_job.error = redact_sensitive_text(str(exc))[:1000]
        failed_job.completed_at = datetime.now(UTC)
        self._record_job_failure(request, failed_job)
        self._session.commit()

    def _record_job_failure(
        self,
        request: ExecutionRequest,
        export_job: WorkspaceExportJob,
    ) -> None:
        actor_user_id = request.requested_by_user_id
        if actor_user_id is None:
            return
        AuditService(self._session).record_user_action(
            workspace_id=request.workspace_id,
            user_id=actor_user_id,
            action="workspace.archive_export_job.failed",
            target_type="workspace_export_job",
            target_id=export_job.id,
            metadata={"error": export_job.error},
        )
