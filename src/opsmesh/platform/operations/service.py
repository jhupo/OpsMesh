"""Persist an idempotent administrator action and its native Workflow intent."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditService
from opsmesh.identity.auth.service import AuthenticationService
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.platform.operations.models import AdminOperationRecord
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.outbox import stage_execution
from opsmesh.shared.db.pagination import page_scalars
from opsmesh.shared.errors import ConflictError, ForbiddenError, NotFoundError
from opsmesh.shared.pagination import PageParams
from opsmesh.workspaces.management.models import Workspace


class AdminOperationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def require_actor(actor: AuthenticatedUser) -> None:
        if not actor.platform_admin or actor.uses_restricted_token:
            raise ForbiddenError("An unrestricted platform administrator is required")

    def submit(
        self,
        *,
        request_id: UUID,
        workspace_id: UUID,
        actor: AuthenticatedUser,
        operation: str,
        parameters: dict[str, object],
    ) -> AdminOperationRecord:
        self.require_actor(actor)
        if operation not in {"verify_audit", "cancel_run"}:
            raise ValueError("Unsupported administrator operation")
        if self._session.get(Workspace, workspace_id) is None:
            raise NotFoundError("Workspace not found")
        previous = self._session.get(AdminOperationRecord, request_id)
        if previous is not None:
            return self._matching(previous, workspace_id, actor, operation, parameters)
        if operation == "cancel_run":
            run_id = UUID(str(parameters["run_id"]))
            if (
                self._session.scalar(
                    select(AgentRun.id).where(
                        AgentRun.workspace_id == workspace_id, AgentRun.id == run_id
                    )
                )
                is None
            ):
                raise NotFoundError("Run not found")
        row = AdminOperationRecord(
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
            previous = self._session.get(AdminOperationRecord, request_id)
            if previous is None:
                raise
            return self._matching(previous, workspace_id, actor, operation, parameters)
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor.user_id,
            action="platform.operation.requested",
            target_type="admin_operation",
            target_id=row.id,
            metadata={"operation": operation, "reason": parameters.get("reason")},
        )
        stage_execution(
            self._session,
            ExecutionRequest(
                execution_id=row.id,
                workspace_id=workspace_id,
                resource_id=row.id,
                kind=ExecutionKind.ADMIN_OPERATION,
                requested_by_user_id=actor.user_id,
            ),
        )
        return row

    @staticmethod
    def _matching(
        row: AdminOperationRecord,
        workspace_id: UUID,
        actor: AuthenticatedUser,
        operation: str,
        parameters: dict[str, object],
    ) -> AdminOperationRecord:
        if (
            row.workspace_id != workspace_id
            or row.actor_user_id != actor.user_id
            or row.actor_token_id != actor.token_id
            or row.operation != operation
            or row.parameters != parameters
        ):
            raise ConflictError("Request ID was already used for another operation")
        return row

    def execution_actor(self, row: AdminOperationRecord) -> AuthenticatedUser:
        actor = AuthenticationService(self._session).refresh_authenticated_user(
            AuthenticatedUser(
                user_id=row.actor_user_id, email="", display_name="", token_id=row.actor_token_id
            )
        )
        self.require_actor(actor)
        return actor

    def get(self, request_id: UUID, *, workspace_id: UUID | None = None) -> AdminOperationRecord:
        query = select(AdminOperationRecord).where(AdminOperationRecord.id == request_id)
        if workspace_id is not None:
            query = query.where(AdminOperationRecord.workspace_id == workspace_id)
        row = self._session.scalar(query)
        if row is None:
            raise NotFoundError("Administrator operation not found")
        return row

    def list(
        self, page: PageParams, *, workspace_id: UUID | None = None
    ) -> tuple[list[AdminOperationRecord], int]:
        query = select(AdminOperationRecord)
        if workspace_id is not None:
            query = query.where(AdminOperationRecord.workspace_id == workspace_id)
        return page_scalars(
            self._session,
            query.order_by(AdminOperationRecord.created_at.desc(), AdminOperationRecord.id.desc()),
            page,
        )
