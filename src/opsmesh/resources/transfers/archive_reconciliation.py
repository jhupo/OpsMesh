"""Operator settlement of uncertain archive output; never an execution retry."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from opsmesh.governance.audit.service import AuditService
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.resources.storage.backends import ObjectStorage, run_storage_operation
from opsmesh.resources.transfers.archive_ownership import archive_export_ownership
from opsmesh.resources.transfers.models import WorkspaceExportJob
from opsmesh.resources.transfers.operation_identity import authorize_archive_reconciliation
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.shared.errors import DomainError
from opsmesh.shared.security.redaction import redact_sensitive_text


class ArchiveExportReconciliationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def reconcile(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        user: AuthenticatedUser,
        reason: str,
        storage: ObjectStorage,
        temporal: Client,
    ) -> WorkspaceExportJob:
        with archive_export_ownership(self._session, job_id):
            authorize_archive_reconciliation(self._session, workspace_id, user)
            job = self._get(workspace_id, job_id)
            if job.status == "reconciled":
                return job
            if job.status not in {"running", "unknown"}:
                raise DomainError(
                    "Only an uncertain archive export can be reconciled",
                    code="archive_export_not_uncertain",
                    status_code=409,
                )
            job.status, job.error = "unknown", "archive_export_cleanup_pending"
            self._session.commit()
            reference = ExecutionRequest(
                execution_id=job_id,
                workspace_id=workspace_id,
                resource_id=job_id,
                kind=ExecutionKind.ARCHIVE_EXPORT,
            )
            handle = temporal.get_workflow_handle(reference.workflow_id)
            try:
                description = await handle.describe(rpc_timeout=timedelta(seconds=10))
                if description.status == WorkflowExecutionStatus.RUNNING:
                    await handle.terminate(
                        reason="Archive export explicitly reconciled",
                        rpc_timeout=timedelta(seconds=10),
                    )
            except RPCError as error:
                if error.status != RPCStatusCode.NOT_FOUND:
                    raise DomainError(
                        "Temporal execution state could not be confirmed",
                        code="archive_export_temporal_unavailable",
                        status_code=503,
                    ) from error
            authorize_archive_reconciliation(self._session, workspace_id, user)
            job = self._get(workspace_id, job_id)
            if job.status != "unknown":
                raise DomainError(
                    "Archive export state changed during reconciliation",
                    code="archive_export_state_changed",
                    status_code=409,
                )
            self._session.commit()
            key = f"workspaces/{workspace_id}/exports/{job_id}/archive.zip"
            await run_storage_operation(lambda: storage.delete(key))
            authorize_archive_reconciliation(self._session, workspace_id, user)
            job = self._get(workspace_id, job_id)
            job.status, job.error, job.completed_at = "reconciled", None, datetime.now(UTC)
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=user.user_id,
                action="workspace.archive_export_job.reconciled",
                target_type="workspace_export_job",
                target_id=job_id,
                metadata={
                    "reason": redact_sensitive_text(reason),
                    "workflow_id": reference.workflow_id,
                },
            )
            self._session.commit()
            return job

    def _get(self, workspace_id: UUID, job_id: UUID) -> WorkspaceExportJob:
        job = self._session.scalar(
            select(WorkspaceExportJob)
            .where(
                WorkspaceExportJob.workspace_id == workspace_id,
                WorkspaceExportJob.id == job_id,
                WorkspaceExportJob.export_type == "workspace_archive",
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job is None:
            raise DomainError(
                "Archive export job not found", code="archive_export_not_found", status_code=404
            )
        return job
