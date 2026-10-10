"""Persist admin intents before execution; Redis is not the only copy of a request."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from opsmesh.governance.audit.chain_schemas import AuditIntegrityCheckResponse
from opsmesh.governance.audit.integrity import AuditIntegrityService
from opsmesh.governance.audit.service import AuditService
from opsmesh.identity.auth.service import AuthenticationService
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.runtime.operations.admin_queries import AdminDiagnosticsService
from opsmesh.runtime.operations.contracts.queue import StaleRunRecoveryRequest
from opsmesh.runtime.operations.models import AdminOperationRequest
from opsmesh.runtime.queues.service import RedisQueue
from opsmesh.runtime.recovery.service import StaleRunRecoveryService
from opsmesh.shared.errors import ConflictError, ForbiddenError


class AdminOperationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def submit(
        self,
        *,
        request_id: UUID,
        workspace_id: UUID,
        actor: AuthenticatedUser,
        operation: str,
        parameters: dict[str, object],
    ) -> AdminOperationRequest:
        if not actor.platform_admin or actor.uses_restricted_token:
            raise ForbiddenError("An unrestricted platform administrator account is required")
        if operation not in {"recover_stale_runs", "verify_audit"}:
            raise ValueError("Unsupported admin operation")
        # Lock the tenant to serialize concurrent submissions with the same request ID.
        workspace = AdminDiagnosticsService(self._session).workspace(workspace_id)
        self._session.refresh(workspace, with_for_update=True)
        previous = self._session.get(AdminOperationRequest, request_id)
        if previous is not None:
            if (
                previous.workspace_id != workspace_id
                or previous.operation != operation
                or previous.parameters != parameters
                or previous.actor_user_id != actor.user_id
            ):
                raise ConflictError("Request ID was already used for another operation")
            return previous
        if operation == "recover_stale_runs" and workspace.status != "active":
            raise ConflictError("Workspace is not active")
        row = AdminOperationRequest(
            id=request_id,
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            actor_token_id=actor.token_id,
            operation=operation,
            parameters=parameters,
            status="pending",
            result={},
        )
        try:
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush([row])
        except IntegrityError:
            # Different tenants do not share the workspace lock, but IDs are global.
            previous = self._session.get(AdminOperationRequest, request_id)
            if previous is None:
                raise
            if (
                previous.workspace_id != workspace_id
                or previous.operation != operation
                or previous.parameters != parameters
                or previous.actor_user_id != actor.user_id
            ):
                raise ConflictError("Request ID was already used for another operation") from None
            return previous
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor.user_id,
            action="platform.operation.requested",
            target_type="admin_operation",
            target_id=row.id,
            metadata={"operation": operation, "reason": parameters.get("reason")},
        )
        self._session.commit()
        return row

    def process_one(self, queue: RedisQueue) -> bool:
        now = datetime.now(UTC)
        # Interrupted operations require operator inspection; do not silently repeat side effects.
        interrupted = self._session.scalars(
            select(AdminOperationRequest)
            .where(
                AdminOperationRequest.status == "running",
                AdminOperationRequest.updated_at < now - timedelta(hours=1),
            )
            .with_for_update(skip_locked=True)
            .limit(10)
        ).all()
        for interrupted_row in interrupted:
            interrupted_row.status = "failed"
            interrupted_row.error_code = "interrupted_outcome_unknown"
            self._record_result(interrupted_row)
        self._session.commit()
        row = self._session.scalar(
            select(AdminOperationRequest)
            .where(
                AdminOperationRequest.status == "pending",
            )
            .order_by(AdminOperationRequest.created_at, AdminOperationRequest.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return False
        row.status = "running"
        request_id = row.id
        self._session.commit()
        try:
            actor = AuthenticationService(self._session).refresh_authenticated_user(
                AuthenticatedUser(
                    user_id=row.actor_user_id,
                    email="",
                    display_name="",
                    token_id=row.actor_token_id,
                )
            )
            if not actor.platform_admin or actor.uses_restricted_token:
                raise ForbiddenError("Administrator authorization was revoked")
            workspace = AdminDiagnosticsService(self._session).workspace(row.workspace_id)
            if row.operation == "verify_audit":
                check = AuditIntegrityService(self._session).check_workspace(workspace.id)
                result = AuditIntegrityCheckResponse.model_validate(check).model_dump(mode="json")
            elif row.operation == "recover_stale_runs":
                if workspace.status != "active":
                    raise ForbiddenError("Workspace is not active")
                request = StaleRunRecoveryRequest.model_validate(row.parameters)
                result = (
                    StaleRunRecoveryService(self._session, queue.redis, queue.keys)
                    .recover(
                        workspace.id,
                        actor_user_id=actor.user_id,
                        stale_after_seconds=request.stale_after_seconds,
                        statuses=list(request.statuses),
                        limit=request.limit,
                        queue_name=queue.queue_name,
                        reason=request.reason,
                    )
                    .model_dump(mode="json")
                )
            else:
                raise ValueError("Unsupported admin operation")
            row.result = result
            row.status = "completed"
            self._record_result(row)
            self._session.commit()
        except Exception:
            self._session.rollback()
            failed = self._session.get(AdminOperationRequest, request_id)
            if failed is not None:
                failed.status = "failed"
                failed.error_code = "operation_failed_inspect_audit_before_retry"
                self._record_result(failed)
                self._session.commit()
        return True

    def _record_result(self, row: AdminOperationRequest) -> None:
        AuditService(self._session).record_system_action(
            workspace_id=row.workspace_id,
            action=f"platform.operation.{row.status}",
            target_type="admin_operation",
            target_id=row.id,
            actor_id="platform_maintenance",
            metadata={
                "operation": row.operation,
                "requested_by": str(row.actor_user_id),
                "error_code": row.error_code,
            },
        )
