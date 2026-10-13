from temporalio import activity

from opsmesh.governance.audit.integrity import AuditIntegrityService
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.runtime.temporal.activities import ActivityDependencies, authorize_execution
from opsmesh.runtime.temporal.contracts import ExecutionRequest


class AuditIntegrityActivities:
    def __init__(self, services: ActivityDependencies) -> None:
        self._services = services

    @activity.defn(name="opsmesh.audit.check_integrity")
    def check_integrity(self, request: ExecutionRequest) -> None:
        if request.resource_id != request.workspace_id:
            raise ValueError("Audit integrity workspace reference differs")
        with self._services.transaction() as session:
            authorize_execution(
                session, request, action=WorkspaceAction.READ, service_operation=True
            )
            AuditIntegrityService(session).verify_and_record_workspace_integrity(
                request.workspace_id
            )
