from sqlalchemy import select
from temporalio import activity

from opsmesh.governance.audit.integrity import AuditIntegrityService
from opsmesh.governance.audit.schemas import AuditIntegrityCheckResponse
from opsmesh.governance.audit.service import AuditService
from opsmesh.identity.authorization.errors import AuthenticationError
from opsmesh.platform.operations.models import AdminOperationRecord
from opsmesh.platform.operations.service import AdminOperationService
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.shared.errors import ForbiddenError


class AdminOperationActivities:
    def __init__(self, services: ActivityDependencies) -> None:
        self._services = services

    @activity.defn(name="opsmesh.platform.execute_operation")
    def execute(self, request: ExecutionRequest) -> None:
        if request.kind != ExecutionKind.ADMIN_OPERATION:
            raise ValueError("Administrator Activity requires a matching operation reference")
        with self._services.transaction() as session:
            row = session.scalar(
                select(AdminOperationRecord)
                .where(
                    AdminOperationRecord.workspace_id == request.workspace_id,
                    AdminOperationRecord.id == request.resource_id,
                )
                .with_for_update()
            )
            if row is None or row.status != "pending":
                return
            service = AdminOperationService(session)
            try:
                actor = service.execution_actor(row)
            except (ForbiddenError, AuthenticationError):
                row.status, row.error_code = "failed", "administrator_authorization_revoked"
            else:
                if row.operation == "verify_audit":
                    result = AuditIntegrityService(session).verify_and_record_workspace_integrity(
                        row.workspace_id
                    )
                    row.result = AuditIntegrityCheckResponse.model_validate(result).model_dump(
                        mode="json"
                    )
                elif row.operation == "cancel_run":
                    from uuid import UUID

                    from opsmesh.orchestration.runs.control import RunControlService

                    run = RunControlService(session).cancel_run(
                        workspace_id=row.workspace_id,
                        run_id=UUID(str(row.parameters["run_id"])),
                        actor_user_id=actor.user_id,
                        commit=False,
                    )
                    if run is None:
                        row.status, row.error_code = "failed", "run_unavailable"
                    else:
                        row.result = {"run_id": str(run.id), "status": run.status}
                else:
                    raise ValueError("Unsupported administrator operation")
                if row.status != "failed":
                    row.status = "completed"
            AuditService(session).record_system_action(
                workspace_id=row.workspace_id,
                action=f"platform.operation.{row.status}",
                target_type="admin_operation",
                target_id=row.id,
                metadata={
                    "operation": row.operation,
                    "requested_by": str(row.actor_user_id),
                    "error_code": row.error_code,
                },
            )
