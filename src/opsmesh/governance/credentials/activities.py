from temporalio import activity

from opsmesh.governance.credentials.service import SecretReencryptionService
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.runtime.temporal.activities import ActivityDependencies, authorize_execution
from opsmesh.runtime.temporal.contracts import ExecutionRequest


class CredentialMaintenanceActivities:
    def __init__(self, services: ActivityDependencies) -> None:
        self._services = services

    @activity.defn(name="opsmesh.credentials.reencrypt")
    def reencrypt(self, request: ExecutionRequest) -> None:
        with self._services.transaction() as session:
            authorize_execution(session, request, action=WorkspaceAction.ADMIN)
            if request.parameters.get("scope") == "global":
                raise ValueError("Workspace execution cannot reencrypt global credentials")
            SecretReencryptionService(session, self._services.secrets()).reencrypt(
                workspace_id=request.workspace_id
            )
