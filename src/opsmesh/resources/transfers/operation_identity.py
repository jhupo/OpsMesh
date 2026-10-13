"""Archive operations retain their accepted identity and read permission ceiling."""

from copy import deepcopy
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.identity.auth.service import AuthenticationService
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.identity.authorization.resource_queries import resource_query_scope
from opsmesh.identity.authorization.service import WorkspaceAuthorizationService
from opsmesh.shared.errors import DomainError
from opsmesh.workspaces.management.models import Workspace


def capture_archive_identity(
    session: Session, workspace_id: UUID, user_id: UUID
) -> dict[str, object]:
    scope = resource_query_scope(session)
    if scope is not None and (scope.workspace_id != workspace_id or scope.user.user_id != user_id):
        raise DomainError(
            "Archive operation identity mismatch", code="archive_identity_denied", status_code=403
        )
    user = (
        AuthenticationService(session).refresh_authenticated_user(scope.user)
        if scope is not None
        else AuthenticationService(session).authenticate_user(user_id)
    )
    WorkspaceAuthorizationService(session).require_workspace(
        user_id=user_id,
        workspace_id=workspace_id,
        action=WorkspaceAction.READ,
        authenticated_user=user,
    )
    return {
        "user_id": str(user_id),
        "token_id": str(user.token_id) if user.token_id else None,
        "token_scopes": deepcopy(user.token_scopes),
    }


def authorize_archive_identity(
    session: Session, workspace_id: UUID, identity: dict[str, object]
) -> AuthenticatedUser:
    workspace = session.get(Workspace, workspace_id, populate_existing=True)
    if workspace is None or workspace.status != "active":
        raise DomainError(
            "Workspace is unavailable", code="archive_workspace_unavailable", status_code=403
        )
    scopes = identity.get("token_scopes")
    if scopes is not None and not isinstance(scopes, dict):
        raise DomainError(
            "Archive token scope is invalid", code="archive_identity_denied", status_code=403
        )
    try:
        captured = AuthenticatedUser(
            user_id=UUID(str(identity["user_id"])),
            email="",
            display_name="",
            token_id=UUID(str(identity["token_id"])) if identity.get("token_id") else None,
            token_scopes=deepcopy(scopes),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise DomainError(
            "Archive initiating identity is unavailable",
            code="archive_identity_denied",
            status_code=403,
        ) from error
    live = AuthenticationService(session).refresh_authenticated_user(captured)
    if live.token_scopes != captured.token_scopes:
        raise DomainError(
            "Archive token scope changed", code="archive_identity_denied", status_code=403
        )
    WorkspaceAuthorizationService(session).require_workspace(
        user_id=live.user_id,
        workspace_id=workspace_id,
        action=WorkspaceAction.READ,
        authenticated_user=live,
    )
    return live


def authorize_archive_source(
    session: Session,
    workspace_id: UUID,
    source_identity: dict[str, object],
    request_identity: dict[str, object],
) -> None:
    if source_identity != request_identity:
        raise DomainError(
            "Archive access requires its accepted identity and token permission ceiling",
            code="archive_identity_denied",
            status_code=403,
        )
    authorize_archive_identity(session, workspace_id, source_identity)


def authorize_archive_reconciliation(
    session: Session, workspace_id: UUID, user: AuthenticatedUser
) -> None:
    live = AuthenticationService(session).refresh_authenticated_user(user)
    WorkspaceAuthorizationService(session).require_workspace(
        user_id=live.user_id,
        workspace_id=workspace_id,
        action=WorkspaceAction.ADMIN,
        authenticated_user=live,
    )
