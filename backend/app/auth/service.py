from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.auth.context import AuthenticatedUser, WorkspaceContext
from backend.app.auth.errors import (
    AuthenticationError,
    PermissionDeniedError,
)
from backend.app.auth.permissions import WorkspaceAction, role_allows
from backend.app.identity.models import User
from backend.app.workspaces.models import Workspace, WorkspaceMember


class AuthorizationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def authenticate_user(self, user_id: UUID) -> AuthenticatedUser:
        user = self._session.get(User, user_id)
        if user is None or user.status != "active":
            raise AuthenticationError("Authenticated user was not found or is inactive")
        return AuthenticatedUser.from_model(user)

    def require_workspace(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID,
        action: WorkspaceAction,
    ) -> WorkspaceContext:
        user = self.authenticate_user(user_id)
        statement = (
            select(Workspace, WorkspaceMember)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(
                Workspace.id == workspace_id,
                Workspace.status == "active",
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.status == "active",
            )
        )
        row = self._session.execute(statement).one_or_none()
        if row is None:
            raise PermissionDeniedError("User is not an active member of this workspace")

        workspace, membership = row
        if not role_allows(membership.role, action):
            raise PermissionDeniedError("Workspace role does not allow this action")

        return WorkspaceContext(user=user, workspace=workspace, membership=membership)

    def ensure_resource_workspace(
        self,
        *,
        resource_workspace_id: UUID,
        expected_workspace_id: UUID,
    ) -> None:
        if resource_workspace_id != expected_workspace_id:
            raise PermissionDeniedError("Resource does not belong to the requested workspace")

