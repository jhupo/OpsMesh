from dataclasses import dataclass
from uuid import UUID

from backend.app.auth.permissions import WorkspaceRole
from backend.app.identity.models import User
from backend.app.workspaces.models import Workspace, WorkspaceMember


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: UUID
    email: str
    display_name: str

    @classmethod
    def from_model(cls, user: User) -> "AuthenticatedUser":
        return cls(user_id=user.id, email=user.email, display_name=user.display_name)


@dataclass(frozen=True)
class WorkspaceContext:
    user: AuthenticatedUser
    workspace: Workspace
    membership: WorkspaceMember

    @property
    def role(self) -> WorkspaceRole:
        return WorkspaceRole(self.membership.role)

