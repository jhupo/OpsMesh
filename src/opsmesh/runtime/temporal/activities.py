from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy.orm import Session
from temporalio.exceptions import ApplicationError

from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.identity.authorization.service import WorkspaceAuthorizationService
from opsmesh.runtime.temporal.contracts import ExecutionRequest
from opsmesh.shared.config import Settings
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.workspaces.management.models import Workspace


@dataclass(frozen=True)
class ActivityDependencies:
    """Worker composition dependencies; session ownership stays with each Activity."""

    sessions: Callable[[], Session]
    settings: Settings

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        with self.sessions() as session:
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def secrets(self) -> SecretEncryptionService:
        return SecretEncryptionService(
            secret=self.settings.credential_encryption_secret,
            key_id=self.settings.credential_encryption_key_id,
            previous_secrets=self.settings.credential_encryption_previous_secrets,
        )


def authorize_execution(
    session: Session,
    request: ExecutionRequest,
    *,
    action: WorkspaceAction,
    service_operation: bool = False,
) -> None:
    workspace = session.get(Workspace, request.workspace_id)
    if workspace is None or workspace.status != "active":
        raise ApplicationError(
            "Workspace is unavailable", type="WorkspaceUnavailable", non_retryable=True
        )
    if request.requested_by_user_id is not None:
        WorkspaceAuthorizationService(session).require_workspace(
            user_id=request.requested_by_user_id,
            workspace_id=request.workspace_id,
            action=action,
        )
    elif not service_operation:
        raise ApplicationError(
            "Execution requires an authorized actor",
            type="ExecutionActorMissing",
            non_retryable=True,
        )


def parameter_int(request: ExecutionRequest, name: str, *, minimum: int = 0) -> int:
    value = request.parameters.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ApplicationError(
            "Invalid execution parameter", type="InvalidExecutionParameter", non_retryable=True
        )
    return value
