from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.identity.auth.service import AuthenticationService
from backend.app.identity.authorization.context import AuthenticatedUser, WorkspaceContext
from backend.app.identity.authorization.errors import PermissionDeniedError
from backend.app.identity.authorization.permissions import (
    WorkspaceAction,
    role_allows,
)
from backend.app.workspaces.management.models import Workspace
from backend.app.workspaces.members.models import WorkspaceMember


class AuthorizationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def require_workspace(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID,
        action: WorkspaceAction,
        authenticated_user: AuthenticatedUser | None = None,
    ) -> WorkspaceContext:
        user = authenticated_user or AuthenticationService(self._session).authenticate_user(user_id)
        if user.user_id != user_id:
            raise PermissionDeniedError("Authenticated user does not match workspace subject")
        if not user.allows_workspace_action(workspace_id, action):
            raise PermissionDeniedError("API token scope does not allow this workspace action")
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

