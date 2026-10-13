"""Durable integrity checks dispatched to the native Temporal execution service."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditService
from opsmesh.platform.updates.admission import require_execution_admission
from opsmesh.resources.storage.backends import ObjectStorage
from opsmesh.resources.transfers.archive_integrity import WorkspaceArchiveIntegrityService
from opsmesh.resources.transfers.contracts import WorkspaceArchiveIntegrityResponse
from opsmesh.resources.transfers.models import WorkspaceArchiveVerification, WorkspaceExportJob
from opsmesh.resources.transfers.operation_identity import (
    authorize_archive_identity,
    authorize_archive_source,
    capture_archive_identity,
)
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.outbox import stage_execution
from opsmesh.shared.errors import DomainError


class ArchiveVerificationOperationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, workspace_id: UUID, job_id: UUID, user_id: UUID, *, origin: str = "user"
    ) -> WorkspaceArchiveVerification:
        require_execution_admission(self._session)
        job = self._session.scalar(
            select(WorkspaceExportJob)
            .where(WorkspaceExportJob.workspace_id == workspace_id, WorkspaceExportJob.id == job_id)
            .with_for_update()
        )
        if job is None or job.status != "completed":
            raise DomainError(
                "A completed archive export is required",
                code="archive_export_not_completed",
                status_code=409,
            )
        identity = capture_archive_identity(self._session, workspace_id, user_id)
        authorize_archive_source(self._session, workspace_id, job.execution_identity, identity)
        active = self._session.scalar(
            select(WorkspaceArchiveVerification).where(
                WorkspaceArchiveVerification.workspace_id == workspace_id,
                WorkspaceArchiveVerification.export_job_id == job_id,
                WorkspaceArchiveVerification.status.in_({"queued", "running"}),
            )
        )
        if active is not None:
            return active
        operation = WorkspaceArchiveVerification(
            workspace_id=workspace_id,
            export_job_id=job_id,
            user_id=user_id,
            status="queued",
            execution_identity=identity,
            origin=origin,
        )
        self._session.add(operation)
        self._session.flush()
        job.verification_status = "queued"
        stage_execution(
            self._session,
            ExecutionRequest(
                execution_id=operation.id,
                workspace_id=workspace_id,
                resource_id=operation.id,
                kind=ExecutionKind.ARCHIVE_VERIFY,
                requested_by_user_id=user_id,
            ),
        )
        self._session.commit()
        return operation

    def get(self, workspace_id: UUID, operation_id: UUID) -> WorkspaceArchiveVerification:
        operation = self._session.scalar(
            select(WorkspaceArchiveVerification)
            .where(
                WorkspaceArchiveVerification.workspace_id == workspace_id,
                WorkspaceArchiveVerification.id == operation_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if operation is None:
            raise DomainError(
                "Archive verification not found",
                code="archive_verification_not_found",
                status_code=404,
            )
        return operation

    def run(self, execution: ExecutionRequest, storage: ObjectStorage) -> None:
        operation = self.get(execution.workspace_id, execution.resource_id)
        if operation.status == "completed":
            return
        if operation.status not in {"queued", "running"}:
            raise DomainError(
                "Archive verification is already settled",
                code="archive_verification_settled",
                status_code=409,
            )
        user = authorize_archive_identity(
            self._session, execution.workspace_id, operation.execution_identity
        )
        if user.user_id != execution.requested_by_user_id:
            raise ValueError("Verification initiating identity mismatch")
        operation.status = "running"
        self._session.commit()
        source = self._session.get(
            WorkspaceExportJob, operation.export_job_id, populate_existing=True
        )
        if source is None:
            raise ValueError("Archive source is unavailable")
        authorize_archive_source(
            self._session,
            execution.workspace_id,
            source.execution_identity,
            operation.execution_identity,
        )
        result = WorkspaceArchiveIntegrityService(self._session).verify_job(
            workspace_id=execution.workspace_id,
            job_id=operation.export_job_id,
            storage=storage,
        )
        authorize_archive_source(
            self._session,
            execution.workspace_id,
            source.execution_identity,
            operation.execution_identity,
        )
        operation = self.get(execution.workspace_id, execution.resource_id)
        operation.status = "completed"
        response = WorkspaceArchiveIntegrityResponse.model_validate(result)
        operation.result = response.model_dump(mode="json")
        job = self._session.get(WorkspaceExportJob, operation.export_job_id)
        assert job is not None
        job.verification_status = "verified" if result["verified"] else "failed"
        job.verified_at = response.checked_at
        AuditService(self._session).record_user_action(
            workspace_id=execution.workspace_id,
            user_id=user.user_id,
            action="workspace.archive_export_job.integrity_checked",
            target_type="workspace_export_job",
            target_id=job.id,
            metadata={
                "verified": response.verified,
                "checks": response.checks,
                "failed_checks": response.failed_checks,
                **response.metadata,
            },
        )
        if operation.origin == "recovery_readiness":
            AuditService(self._session).record_user_action(
                workspace_id=execution.workspace_id,
                user_id=user.user_id,
                action="workspace.recovery_readiness.archive_integrity_verified",
                target_type="workspace_export_job",
                target_id=job.id,
                metadata={
                    "operation_id": str(operation.id),
                    "verified": result["verified"],
                    "failed_checks": result["failed_checks"],
                },
            )
        self._session.commit()

    def failed(self, execution: ExecutionRequest) -> None:
        operation = self.get(execution.workspace_id, execution.resource_id)
        if operation.status not in {"queued", "running"}:
            return
        operation.status, operation.error_code = "failed", "archive_verification_interrupted"
        job = self._session.get(WorkspaceExportJob, operation.export_job_id)
        if job is not None:
            job.verification_status = "failed"
        self._session.commit()
