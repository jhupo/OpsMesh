"""Durable receipts for disposable restore writes and explicit uncertain cleanup."""

from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, Table, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditService
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.platform.updates.admission import require_execution_admission
from opsmesh.resources.files.security import validate_storage_key
from opsmesh.resources.storage.backends import ObjectStorage
from opsmesh.resources.transfers.contracts import WorkspaceArchiveRestoreDrillRequest
from opsmesh.resources.transfers.models import WorkspaceExportJob, WorkspaceRestoreDrillOperation
from opsmesh.resources.transfers.operation_identity import (
    authorize_archive_reconciliation,
    authorize_archive_source,
    capture_archive_identity,
)
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.outbox import stage_execution
from opsmesh.shared.db.pagination import page_scalars
from opsmesh.shared.errors import DomainError
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.security.redaction import redact_sensitive_text
from opsmesh.workspaces.management.models import Workspace


def restore_drill_engine(session: Session) -> Engine:
    bind = session.get_bind()
    return bind.engine if isinstance(bind, Connection) else bind


@contextmanager
def restore_drill_lock(engine: Engine, workspace_id: UUID) -> Iterator[None]:
    """No transaction is held; backend death releases the process ownership lock."""
    key = int.from_bytes(
        sha256(b"restore-drill:" + workspace_id.bytes).digest()[:8], "big", signed=True
    )
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        if not connection.scalar(select(func.pg_try_advisory_lock(key))):
            raise DomainError(
                "A workspace restore drill is still executing",
                code="restore_drill_busy",
                status_code=409,
            )
        try:
            yield
        finally:
            try:
                connection.execute(select(func.pg_advisory_unlock(key)))
            except SQLAlchemyError:
                connection.invalidate()


class RestoreDrillOperationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        workspace_id: UUID,
        job_id: UUID,
        user_id: UUID,
        request: WorkspaceArchiveRestoreDrillRequest,
        *,
        origin: str = "user",
    ) -> WorkspaceRestoreDrillOperation:
        require_execution_admission(self._session)
        workspace = self._session.scalar(
            select(Workspace).where(Workspace.id == workspace_id).with_for_update()
        )
        if workspace is None or workspace.status != "active":
            raise ValueError("Workspace is unavailable")
        operations = cast(Table, WorkspaceRestoreDrillOperation.__table__)
        # Workspace occupancy must include operations private to other initiating users.
        previous = self._session.scalars(
            select(operations.c.status)
            .where(
                operations.c.workspace_id == workspace_id,
                operations.c.status.in_({"queued", "running", "unknown"}),
            )
            .with_for_update()
        ).all()
        if previous:
            if "running" in previous:
                try:
                    with restore_drill_lock(restore_drill_engine(self._session), workspace_id):
                        self._session.execute(
                            update(operations)
                            .where(
                                operations.c.workspace_id == workspace_id,
                                operations.c.status == "running",
                            )
                            .values(status="unknown", error_code="restore_drill_outcome_unknown")
                        )
                        self._session.commit()
                except DomainError as error:
                    if error.code != "restore_drill_busy":
                        raise
            raise DomainError(
                "An earlier restore drill is active or requires explicit reconciliation",
                code="restore_drill_active",
                status_code=409,
            )
        export = self._session.scalar(
            select(WorkspaceExportJob).where(
                WorkspaceExportJob.workspace_id == workspace_id,
                WorkspaceExportJob.id == job_id,
                WorkspaceExportJob.status == "completed",
            )
        )
        if export is None:
            raise DomainError(
                "A completed workspace archive export is required",
                code="archive_export_not_completed",
                status_code=409,
            )
        identity = capture_archive_identity(self._session, workspace_id, user_id)
        authorize_archive_source(self._session, workspace_id, export.execution_identity, identity)
        operation = WorkspaceRestoreDrillOperation(
            workspace_id=workspace_id,
            export_job_id=job_id,
            user_id=user_id,
            temporary_workspace_id=uuid4(),
            status="queued",
            storage_keys=[],
            request=request.model_dump(mode="json"),
            execution_identity=identity,
            origin=origin,
        )
        self._session.add(operation)
        self._session.flush()
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="workspace.archive_restore_drill.queued",
            target_type="workspace_restore_drill_operation",
            target_id=operation.id,
            metadata={"source_export_job_id": str(job_id)},
        )
        stage_execution(
            self._session,
            ExecutionRequest(
                execution_id=operation.id,
                workspace_id=workspace_id,
                resource_id=operation.id,
                kind=ExecutionKind.ARCHIVE_RESTORE_DRILL,
                requested_by_user_id=user_id,
            ),
        )
        self._session.commit()
        return operation

    def finish(
        self,
        workspace_id: UUID,
        operation_id: UUID,
        status: str,
        error: str | None,
        *,
        result: dict[str, object] | None = None,
    ) -> None:
        operation = self._get(workspace_id, operation_id, lock=True)
        operation.status, operation.error_code = status, error
        operation.result = result
        self._session.commit()

    def list(
        self, workspace_id: UUID, page: PageParams
    ) -> tuple[list[WorkspaceRestoreDrillOperation], int]:
        return page_scalars(
            self._session,
            select(WorkspaceRestoreDrillOperation)
            .where(WorkspaceRestoreDrillOperation.workspace_id == workspace_id)
            .order_by(
                WorkspaceRestoreDrillOperation.created_at.desc(), WorkspaceRestoreDrillOperation.id
            ),
            page,
        )

    def get(self, workspace_id: UUID, operation_id: UUID) -> WorkspaceRestoreDrillOperation:
        operation = self._get(workspace_id, operation_id)
        if operation.status == "running":
            try:
                with restore_drill_lock(restore_drill_engine(self._session), workspace_id):
                    current = self._get(workspace_id, operation_id, lock=True)
                    if current.status == "running":
                        self.finish(
                            workspace_id, operation_id, "unknown", "restore_drill_outcome_unknown"
                        )
            except DomainError as failure:
                if failure.code != "restore_drill_busy":
                    raise
        return operation

    def reconcile(
        self,
        workspace_id: UUID,
        operation_id: UUID,
        user: AuthenticatedUser,
        reason: str,
        storage: ObjectStorage,
    ) -> WorkspaceRestoreDrillOperation:
        authorize_archive_reconciliation(self._session, workspace_id, user)
        with restore_drill_lock(restore_drill_engine(self._session), workspace_id):
            operation = self._get(workspace_id, operation_id, lock=True)
            if operation.status == "reconciled":
                return operation
            if operation.status not in {"unknown", "running"}:
                raise DomainError(
                    "Only an uncertain restore drill can be reconciled",
                    code="restore_drill_not_uncertain",
                    status_code=409,
                )
            operation.status, operation.error_code = "unknown", "restore_drill_cleanup_pending"
            keys, temporary_id = list(operation.storage_keys), operation.temporary_workspace_id
            self._session.commit()
            for key in keys:
                validate_drill_storage_key(key, temporary_id)
                storage.delete(key)
            authorize_archive_reconciliation(self._session, workspace_id, user)
            operation = self._get(workspace_id, operation_id, lock=True)
            operation.status, operation.error_code = "reconciled", None
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=user.user_id,
                action="workspace.archive_restore_drill.reconciled",
                target_type="workspace_restore_drill_operation",
                target_id=operation_id,
                metadata={"reason": redact_sensitive_text(reason), "objects_cleaned": len(keys)},
            )
            self._session.commit()
            return operation

    def _get(
        self, workspace_id: UUID, operation_id: UUID, *, lock: bool = False
    ) -> WorkspaceRestoreDrillOperation:
        statement = (
            select(WorkspaceRestoreDrillOperation)
            .where(
                WorkspaceRestoreDrillOperation.workspace_id == workspace_id,
                WorkspaceRestoreDrillOperation.id == operation_id,
            )
            .execution_options(populate_existing=True)
        )
        operation = self._session.scalar(statement.with_for_update() if lock else statement)
        if operation is None:
            raise DomainError(
                "Restore drill operation not found", code="restore_drill_missing", status_code=404
            )
        return operation


def validate_drill_storage_key(key: str, temporary_workspace_id: UUID) -> None:
    prefix = f"workspaces/{temporary_workspace_id}/"
    try:
        normalized = validate_storage_key(key, expected_prefix=prefix)
    except ValueError:
        normalized = ""
    if (
        normalized != key
        or not key.startswith(prefix)
        or key[len(prefix) :].split("/", 1)[0]
        not in {
            "files",
            "artifacts",
        }
    ):
        raise DomainError(
            "Restore receipt contains an invalid storage scope",
            code="restore_drill_receipt_invalid",
            status_code=409,
        )
